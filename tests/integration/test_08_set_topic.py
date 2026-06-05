"""Integration: GerritClient.set_topic sets and clears the topic on a change."""

from __future__ import annotations

import secrets

import pytest

from gerrit_workflow_tools.cli_push import main as ger_push_main
from gerrit_workflow_tools.core.gerrit.rest import GerritClient
from tests.conftest import run_cli
from tests.integration.gerrit_http import GerritHttpSession, quote_change_id
from tests.integration.integration_helpers import (
    first_change_id_from_tip,
    prepare_topic_repo,
)
from tests.integration.repo_builder import build_linear_chain


def test_set_topic_sets_and_clears(
    tmp_path,
    gerrit_integration_context,
    gerrit_admin_session: GerritHttpSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GerritClient.set_topic must set a non-empty topic and clear it without raising."""
    ctx = gerrit_integration_context
    topic_branch = f"st_{secrets.token_hex(4)}"
    repo = prepare_topic_repo(ctx, tmp_path, topic_branch)
    build_linear_chain(repo, ["feat: set_topic integration test"])

    code, _out, err = run_cli(repo, ger_push_main, ["--yes", "--no-rebase-check"], monkeypatch)
    assert code == 0, err

    change_id = first_change_id_from_tip(gerrit_admin_session, ctx.project_verified, topic_branch)
    assert change_id, "expected an open change after push"

    # GerritClient reads credentials from the git config set by prepare_topic_repo.
    client = GerritClient(ctx.http_base, cwd=str(repo))

    # --- set a non-empty topic ---
    new_topic = f"my-topic-{secrets.token_hex(3)}"
    client.set_topic(change_id, new_topic)  # must not raise

    enc = quote_change_id(change_id)
    detail = gerrit_admin_session.get_json(f"changes/{enc}/detail")
    assert isinstance(detail, dict)
    assert detail.get("topic") == new_topic, f"expected topic {new_topic!r} on Gerrit, got {detail.get('topic')!r}"

    # --- clear the topic ---
    client.set_topic(change_id, None)  # must not raise

    detail_after = gerrit_admin_session.get_json(f"changes/{enc}/detail")
    assert isinstance(detail_after, dict)
    assert not detail_after.get("topic"), f"expected no topic after clearing, got {detail_after.get('topic')!r}"
