"""Checkpoint 7: ``ger log``, ``ger show``, and ``ger push --dry-run`` against a live remote."""

from __future__ import annotations

import re
import secrets

import pytest

from gerrit_workflow_tools.cli_log import main as ger_log_main
from gerrit_workflow_tools.cli_push import main as ger_push_main
from gerrit_workflow_tools.cli_show import main as ger_show_main
from gerrit_workflow_tools.core.git_run import git_out
from tests.conftest import run_cli
from tests.integration.gerrit_http import GerritHttpSession
from tests.integration.gerrit_seed import post_review_labels
from tests.integration.integration_helpers import open_changes_on_branch, prepare_topic_repo
from tests.integration.repo_builder import build_linear_chain


def test_ger_log_show_and_push_dry_run(
    tmp_path,
    gerrit_integration_context,
    gerrit_admin_session: GerritHttpSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    topic = f"cmd_{secrets.token_hex(5)}"
    repo = prepare_topic_repo(gerrit_integration_context, tmp_path, topic)
    build_linear_chain(repo, ["first on server", "second on server"])
    code, _out, err = run_cli(
        repo,
        ger_push_main,
        ["--yes", "--no-rebase-check"],
        monkeypatch,
    )
    assert code == 0, err

    proj = gerrit_integration_context.project_verified
    rows = open_changes_on_branch(gerrit_admin_session, proj, topic)
    assert len(rows) >= 2
    for row in rows[:2]:
        cid = str(row.get("change_id") or row.get("id"))
        post_review_labels(gerrit_admin_session, cid, code_review=2, verified=1)

    code_log, out_log, elog = run_cli(
        repo,
        ger_log_main,
        ["--color", "never", "--show-change-id"],
        monkeypatch,
    )
    assert code_log == 0, elog
    # Text mode appends a shortened Change-Id token (not a "Change-Id:" footer line).
    assert re.search(r"I[a-f0-9]{8}", out_log, re.IGNORECASE), out_log

    tip = git_out("rev-parse", "HEAD", cwd=repo).strip()
    code_show, out_show, eshow = run_cli(repo, ger_show_main, ["--color", "never", tip], monkeypatch)
    assert code_show == 0, eshow
    assert "Code-Review" in out_show or "change" in out_show.lower()

    code_dr, out_dr, edr = run_cli(
        repo,
        ger_push_main,
        ["--dry-run", "--no-rebase-check"],
        monkeypatch,
    )
    assert code_dr == 0, edr
    assert "refs/for/" in out_dr or "git push" in out_dr
