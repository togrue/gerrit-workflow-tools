# Spec: docu/spec/commands/serve.md (Gerrit's event stream, GET /activity)
"""``ger serve`` and the event stream: fallback, invalidation through a request, the activity long-poll."""

from __future__ import annotations

import io
import json
import sys
import threading
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_serve import main as serve_main
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.cache import GerritCache
from gerrit_workflow_tools.core.gerrit.event_stream import EventStream, StreamState
from gerrit_workflow_tools.core.git_run import git
from gerrit_workflow_tools.server.activity import Activity, follow_events
from gerrit_workflow_tools.server.api import GerApi
from gerrit_workflow_tools.server.httpd import GerHttpServer
from tests.change_store import ChangeStore
from tests.event_fakes import HOLD, End, Pushed, ScriptedEventSource
from tests.test_serve import V1, WEB, _gerrit, _get


def _following(
    repo: Path, store: ChangeStore, source: ScriptedEventSource
) -> tuple[GerApi, EventStream, threading.Lock]:
    activity = Activity()
    api = GerApi(repo, gerrit=store, activity=activity)
    lock = threading.Lock()
    stream = follow_events(repo, Settings.from_cwd(repo), activity=activity, lock=lock, gerrit=store, source=source)
    return api, stream, lock


def _reaches(activity: Activity, n: int, timeout: float = 5.0) -> bool:
    """Wait until the activity sequence is at least *n*."""
    deadline = time.monotonic() + timeout
    while activity.sequence < n and time.monotonic() < deadline:
        activity.wait(activity.sequence, 1)
    return activity.sequence >= n


def _without_clock(answer: Any) -> Any:
    """*answer* without what depends on the time it was asked (``generated``, ages in seconds)."""
    if isinstance(answer, dict):
        return {k: _without_clock(v) for k, v in answer.items() if k != "generated" and not k.endswith("_seconds")}
    return [_without_clock(item) for item in answer] if isinstance(answer, list) else answer


def test_without_a_live_stream_ger_serve_answers_and_asks_gerrit_exactly_as_before(stack_repo: Path) -> None:
    plain_store, _keys, _rows = _gerrit(stack_repo)
    plain = [_without_clock(_get(GerApi(stack_repo, gerrit=plain_store), "/overview")) for _ in range(2)]

    git("config", "ger.eventStream", "on", cwd=stack_repo)
    store, _keys, _rows = _gerrit(stack_repo)
    GerritCache.for_web_base(WEB).clear()
    source = ScriptedEventSource([End("Permission denied (publickey).")], [End("Permission denied (publickey).")])
    api, stream, _lock = _following(stack_repo, store, source)
    stream.start()
    assert source.wait_attempts(1)
    following = [_without_clock(_get(api, "/overview")) for _ in range(2)]
    stream.stop()

    assert following == plain
    assert [(c.method, c.args) for c in store.calls] == [(c.method, c.args) for c in plain_store.calls]


def test_an_event_drops_that_change_only_and_raises_the_activity_sequence(stack_repo: Path) -> None:
    store, keys, _rows = _gerrit(stack_repo)
    connection = Pushed()
    api, stream, _lock = _following(stack_repo, store, ScriptedEventSource(connection))
    stream.start()
    _get(api, "/stack")
    before = api.activity.sequence
    asked = len(store.queries())

    payload = store.get_change(keys[1])
    change = {"project": payload["project"], "branch": "main", "id": payload["change_id"], "number": payload["_number"]}
    connection.push(json.dumps({"type": "comment-added", "change": change, "author": {"username": "alice"}}))
    assert _reaches(api.activity, before + 2)  # live, then the event: a client looks again

    _get(api, "/stack")
    stream.stop()
    again = store.queries()[asked:]
    assert again and all(payload["change_id"] in query for query in again)  # only 101 is asked for again
    assert not any(store.get_change(key)["change_id"] in " ".join(again) for key in (keys[0], keys[2], keys[3]))


def test_events_about_other_projects_leave_the_sequence_alone(stack_repo: Path) -> None:
    store, _keys, _rows = _gerrit(stack_repo)
    connection = Pushed()
    api, stream, _lock = _following(stack_repo, store, ScriptedEventSource(connection))
    stream.start()
    other = {"project": "elsewhere", "branch": "main", "id": "I" + "e" * 40, "number": 4242}
    connection.push(json.dumps({"type": "comment-added", "change": other}))
    connection.push(json.dumps({"type": "dropped-output"}))
    assert _reaches(api.activity, 2)
    settled = api.activity.wait(api.activity.sequence, 0.3)
    stream.stop()
    # Live (1) and dropped-output (2); the other project's comment raised nothing.
    assert settled == 2


def test_health_says_whether_the_stream_is_followed(stack_repo: Path) -> None:
    store, _keys, _rows = _gerrit(stack_repo)
    assert _get(GerApi(stack_repo, gerrit=store), "/health")["events"] == {
        "state": "off",
        "reason": "not followed",
        "source": None,
    }
    api, stream, _lock = _following(stack_repo, store, ScriptedEventSource([json.dumps({"type": "x"}), HOLD]))
    stream.start()
    assert api.activity.wait(0, 5) == 1
    health = _get(api, "/health")
    stream.stop()
    assert health["events"] == {"state": "live", "reason": None, "source": "ssh://events.example:29418"}
    assert health["activity"] == 1


def test_an_injected_gerrit_without_an_event_source_never_tries_ssh(stack_repo: Path) -> None:
    store, _keys, _rows = _gerrit(stack_repo)
    stream = follow_events(
        stack_repo, Settings.from_cwd(stack_repo), activity=Activity(), lock=threading.Lock(), gerrit=store
    )
    assert stream.status.state is StreamState.OFF
    assert stream.status.reason == "no event source for an injected Gerrit"


def test_off_and_no_host_are_off_with_a_reason(tmp_path: Path) -> None:
    git("init", "-q", cwd=tmp_path)
    no_host = follow_events(tmp_path, Settings.from_cwd(tmp_path), activity=Activity(), lock=threading.Lock())
    assert (no_host.status.state, no_host.status.reason) == (StreamState.OFF, "gerrit.webUrl is not set")
    git("config", "gerrit.webUrl", WEB, cwd=tmp_path)
    git("config", "ger.eventStream", "off", cwd=tmp_path)
    off = follow_events(tmp_path, Settings.from_cwd(tmp_path), activity=Activity(), lock=threading.Lock())
    assert (off.status.state, off.status.reason) == (StreamState.OFF, "ger.eventStream is off")


# --- Over HTTP ---------------------------------------------------------------------------


@pytest.fixture
def served(stack_repo: Path) -> Iterator[GerHttpServer]:
    store, _keys, _rows = _gerrit(stack_repo)
    server = GerHttpServer(GerApi(stack_repo, gerrit=store), token="t0ken")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.api.activity.close()
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def _http(server: GerHttpServer, path: str, token: str = "t0ken") -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(server.url + path, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_activity_long_polls_without_holding_up_other_requests(served: GerHttpServer) -> None:
    assert _http(served, f"{V1}/activity")[1]["activity"] == 0  # no since: at once
    assert _http(served, f"{V1}/activity", token="wrong")[0] == 401
    answers: list[dict[str, Any]] = []
    waiter = threading.Thread(target=lambda: answers.append(_http(served, f"{V1}/activity?since=0&wait=20")[1]))
    started = time.monotonic()
    waiter.start()
    time.sleep(0.2)
    assert _http(served, f"{V1}/health")[0] == 200  # answered while the long-poll waits
    assert not answers
    served.api.activity.bump()
    waiter.join(timeout=5)
    assert answers and answers[0]["activity"] == 1 and time.monotonic() - started < 5


def test_activity_answers_after_the_wait_when_nothing_moved(served: GerHttpServer) -> None:
    started = time.monotonic()
    assert _http(served, f"{V1}/activity?since=0&wait=0.3")[0] == 400  # whole seconds only
    status, body = _http(served, f"{V1}/activity?since=0&wait=1")
    assert status == 200 and body["activity"] == 0 and 0.9 < time.monotonic() - started < 5


# --- The service as started -----------------------------------------------------------------


def test_serve_stops_with_stdin_and_takes_the_stream_with_it(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, _keys, _rows = _gerrit(stack_repo)
    source = ScriptedEventSource([json.dumps({"type": "dropped-output"}), HOLD])
    monkeypatch.chdir(stack_repo)
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"")))
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    started = time.monotonic()
    code = serve_main(["--exit-with-stdin"], gerrit=store, events=source)
    assert code == ExitCode.OK
    assert time.monotonic() - started < 10
    assert json.loads(out.getvalue().splitlines()[0])["url"].startswith("http://127.0.0.1:")
    assert all(connection.closed for connection in source.connections)
