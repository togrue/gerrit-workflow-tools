"""Integration: ``ger show`` / ``ger log`` must pick up comment resolves without a cache clear.

Gerrit often stores ``ChangeInfo.updated`` at second precision. A web-UI reply+resolve in
the same second leaves that timestamp unchanged, so a probe that only compares ``updated``
serves stale comments until the cache is cleared. These tests expire the trust window and
assert the follow-up cache still invalidates.
"""

from __future__ import annotations

import json
import secrets

import pytest

from gerrit_workflow_tools.cli_log import main as ger_log_main
from gerrit_workflow_tools.cli_push import main as ger_push_main
from gerrit_workflow_tools.cli_show import main as ger_show_main
from tests.conftest import run_cli
from tests.helpers import force_zero_change_trust_window
from tests.integration.gerrit_http import GerritHttpSession
from tests.integration.integration_helpers import (
    open_changes_on_branch,
    post_unresolved_inline_comment,
    prepare_topic_repo,
    resolve_unresolved_inline_comments,
)
from tests.integration.repo_builder import build_linear_chain


def _json(stdout: str) -> dict:
    return json.loads(stdout)


def test_ger_show_and_log_refresh_after_comment_resolve(
    tmp_path,
    gerrit_integration_context,
    gerrit_admin_session: GerritHttpSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    force_zero_change_trust_window(monkeypatch)
    topic = f"cmt_cache_{secrets.token_hex(5)}"
    repo = prepare_topic_repo(gerrit_integration_context, tmp_path, topic)
    build_linear_chain(repo, ["comment cache subject"])
    code, _out, err = run_cli(repo, ger_push_main, ["--yes", "--no-rebase-check"], monkeypatch)
    assert code == 0, err

    proj = gerrit_integration_context.project_verified
    rows = open_changes_on_branch(gerrit_admin_session, proj, topic)
    assert rows
    change_id = str(rows[0].get("change_id") or rows[0].get("id"))
    post_unresolved_inline_comment(
        gerrit_admin_session,
        change_id,
        "chain_0.txt",
        1,
        "please address this",
    )

    code_show, out_show, eshow = run_cli(repo, ger_show_main, ["--json", "--color", "never", "HEAD"], monkeypatch)
    assert code_show == 1, eshow
    shown = _json(out_show)
    assert shown.get("comments_unresolved") == 1
    assert any("please address this" in str(c.get("message")) for c in shown.get("comments") or [])

    code_log, out_log, elog = run_cli(repo, ger_log_main, ["--json", "--color", "never"], monkeypatch)
    assert code_log == 1, elog
    logged = _json(out_log)
    tip = next(c for c in logged["commits"] if c.get("change_id") == shown.get("change_id"))
    assert tip["comments_unresolved"] == 1

    n = resolve_unresolved_inline_comments(gerrit_admin_session, change_id, message="fixed")
    assert n >= 1

    code_show2, out_show2, eshow2 = run_cli(repo, ger_show_main, ["--json", "--color", "never", "HEAD"], monkeypatch)
    assert code_show2 in (0, 1), eshow2
    shown2 = _json(out_show2)
    assert shown2.get("comments") == []
    assert shown2.get("comments_unresolved") == 0

    code_log2, out_log2, elog2 = run_cli(repo, ger_log_main, ["--json", "--color", "never"], monkeypatch)
    assert code_log2 in (0, 1), elog2
    logged2 = _json(out_log2)
    tip2 = next(c for c in logged2["commits"] if c.get("change_id") == shown.get("change_id"))
    assert tip2["comments_unresolved"] == 0
