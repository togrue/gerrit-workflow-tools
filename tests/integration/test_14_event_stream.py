"""Gerrit's event stream over real ssh: refused without the capability, live with it, events drop cache rows.

Uses a throwaway ssh key generated in a temp directory, an empty ssh config and a temp
known_hosts file: the user's own ``~/.ssh`` is neither read nor written. Skipped when OpenSSH
(``ssh``, ``ssh-keygen``) is not on PATH.
"""

from __future__ import annotations

import json
import secrets
import shutil
import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
import requests

from gerrit_workflow_tools.core.gerrit.cache import GerritCache
from gerrit_workflow_tools.core.gerrit.event_source import SshEventSource, SshTarget
from gerrit_workflow_tools.core.gerrit.event_stream import (
    EventScope,
    EventStream,
    GerritEvent,
    StreamMode,
    StreamState,
    StreamStatus,
    apply_event,
)
from tests.integration.conftest import GerritIntegrationContext
from tests.integration.gerrit_http import GerritHttpSession


pytestmark = pytest.mark.skipif(
    shutil.which("ssh") is None or shutil.which("ssh-keygen") is None,
    reason="needs OpenSSH (ssh and ssh-keygen) on PATH",
)


@pytest.fixture
def throwaway_ssh(tmp_path: Path) -> tuple[str, list[str]]:
    """A new key pair, and the ssh arguments that use it and nothing of the user's own setup."""
    key = tmp_path / "id_ed25519"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "ger-it", "-f", str(key)], check=True)
    config = tmp_path / "ssh_config"
    config.write_text("", encoding="utf-8")
    extra = ["-F", str(config), "-i", str(key), "-o", "IdentitiesOnly=yes", "-o", "IdentityAgent=none"]
    extra += ["-o", f"UserKnownHostsFile={tmp_path / 'known_hosts'}", "-o", "StrictHostKeyChecking=accept-new"]
    return (tmp_path / "id_ed25519.pub").read_text(encoding="utf-8").strip(), extra


def _add_ssh_key(ctx: GerritIntegrationContext, user: str, password: str, public_key: str) -> None:
    response = requests.post(
        f"{ctx.http_base}/a/accounts/self/sshkeys",
        data=public_key.encode("utf-8"),
        headers={"Content-Type": "text/plain"},
        auth=(user, password),
        timeout=30,
    )
    assert response.status_code in (200, 201), response.text[:300]


class Watch:
    """Statuses and events as the reader reports them, waitable."""

    def __init__(self) -> None:
        self.statuses: list[StreamStatus] = []
        self.events: list[GerritEvent] = []
        self._changed = threading.Condition()

    def on_status(self, status: StreamStatus) -> None:
        with self._changed:
            self.statuses.append(status)
            self._changed.notify_all()

    def on_event(self, event: GerritEvent) -> None:
        with self._changed:
            self.events.append(event)
            self._changed.notify_all()

    def wait(self, predicate: Any, timeout: float = 60.0) -> bool:
        with self._changed:
            return self._changed.wait_for(predicate, timeout)


def _stream(ctx: GerritIntegrationContext, user: str, extra: list[str], watch: Watch) -> EventStream:
    source = SshEventSource(SshTarget(ctx.public_host, ctx.host_ssh_port, user), extra=extra)
    return EventStream(source, mode=StreamMode.AUTO, on_event=watch.on_event, on_status=watch.on_status, grace=3.0)


@pytest.fixture
def stream_events_group(
    gerrit_integration_context: GerritIntegrationContext, gerrit_admin_session: GerritHttpSession
) -> Iterator[str]:
    """A group holding the dev account, granted the Stream Events capability for the test only."""
    ctx = gerrit_integration_context
    name = f"it_stream_{ctx.run_id}_{secrets.token_hex(3)}"
    group = gerrit_admin_session.put_json(f"groups/{quote(name, safe='')}", body={"members": [ctx.dev_user]})
    rule = {"GLOBAL_CAPABILITIES": {"permissions": {"streamEvents": {"rules": {group["id"]: {"action": "ALLOW"}}}}}}
    gerrit_admin_session.post_json("projects/All-Projects/access", body={"add": rule})
    try:
        yield name
    finally:
        gerrit_admin_session.post_json("projects/All-Projects/access", body={"remove": rule})


def test_without_the_capability_auto_gives_up_once_and_says_why(
    gerrit_integration_context: GerritIntegrationContext, throwaway_ssh: tuple[str, list[str]]
) -> None:
    ctx = gerrit_integration_context
    public_key, extra = throwaway_ssh
    _add_ssh_key(ctx, ctx.rev_alt_user, ctx.rev_alt_password, public_key)
    watch = Watch()
    stream = _stream(ctx, ctx.rev_alt_user, extra, watch)
    stream.start()
    try:
        assert watch.wait(lambda: stream.status.state is StreamState.UNAVAILABLE), watch.statuses
    finally:
        stream.stop()
    reason = stream.status.reason or ""
    print(f"refused: {reason}")
    assert "stream" in reason.lower() or "capability" in reason.lower() or "not permitted" in reason.lower()
    assert StreamState.LIVE not in [status.state for status in watch.statuses]


def test_with_the_capability_events_arrive_and_drop_exactly_their_change(
    gerrit_integration_context: GerritIntegrationContext,
    gerrit_dev_session: GerritHttpSession,
    stream_events_group: str,
    throwaway_ssh: tuple[str, list[str]],
    tmp_path: Path,
) -> None:
    ctx = gerrit_integration_context
    public_key, extra = throwaway_ssh
    _add_ssh_key(ctx, ctx.dev_user, ctx.dev_password, public_key)
    watch = Watch()
    stream = _stream(ctx, ctx.dev_user, extra, watch)
    stream.start()
    try:
        assert watch.wait(lambda: stream.status.state is StreamState.LIVE), watch.statuses

        project = ctx.project_plain
        created = gerrit_dev_session.post_json(
            "changes/", body={"project": project, "branch": "main", "subject": f"events {secrets.token_hex(3)}"}
        )
        other = gerrit_dev_session.post_json(
            "changes/", body={"project": project, "branch": "main", "subject": "other"}
        )
        number, other_number = created["_number"], other["_number"]
        assert watch.wait(lambda: any(e.type == "patchset-created" and e.number == number for e in watch.events))
        event = next(e for e in watch.events if e.number == number)
        assert (event.project, event.branch, event.change_id) == (project, "main", created["change_id"])

        cache = GerritCache(tmp_path / "cache.db", web_base=ctx.http_base)
        for info in (created, other):
            cache.upsert_changes({str(info["_number"]): info})
        gerrit_dev_session.post_json(f"changes/{project}~{number}/revisions/current/review", body={"message": "ok"})
        assert watch.wait(lambda: any(e.type == "comment-added" and e.number == number for e in watch.events))
        comment = next(e for e in watch.events if e.type == "comment-added" and e.number == number)
        assert apply_event(comment, cache=cache, scope=EventScope.of([project])) is True
        remaining = cache.find_payloads_by_footer_change_ids([created["change_id"], other["change_id"]])
        assert remaining[created["change_id"]] == []
        assert [p["_number"] for p in remaining[other["change_id"]]] == [other_number]
    finally:
        stream.stop()
    print(json.dumps([[e.type, e.number] for e in watch.events]))
