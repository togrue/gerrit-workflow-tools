"""Checkpoint 3-5: clone, topic branches, ger push (ready prefix, --all), branch isolation."""

from __future__ import annotations

import secrets

import pytest

from gerrit_workflow_tools.cli_push import main as ger_push_main
from gerrit_workflow_tools.core.git_run import git_out
from tests.conftest import run_cli
from tests.integration.gerrit_http import GerritHttpSession
from tests.integration.integration_helpers import (
    open_changes_on_branch,
    prepare_topic_repo,
)
from tests.integration.repo_builder import build_linear_chain


def test_clone_and_chain_has_change_ids(
    tmp_path,
    gerrit_integration_context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    topic = f"chain_{secrets.token_hex(4)}"
    repo = prepare_topic_repo(gerrit_integration_context, tmp_path, topic)
    build_linear_chain(repo, ["alpha commit", "beta commit"])
    body = git_out("log", "-1", "--format=%B", cwd=repo)
    assert "Change-Id:" in body


def test_push_ready_prefix_stops_at_wip(
    tmp_path,
    gerrit_integration_context,
    gerrit_admin_session: GerritHttpSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    topic = f"rp_{secrets.token_hex(4)}"
    repo = prepare_topic_repo(gerrit_integration_context, tmp_path, topic)
    build_linear_chain(
        repo,
        [
            "feat: first commit",
            "feat: second commit",
            "wip: blocks here",
            "feat: fourth commit",
        ],
    )
    code, _out, err = run_cli(
        repo,
        ger_push_main,
        ["--yes", "--no-rebase-check"],
        monkeypatch,
    )
    assert code == 0, err
    proj = gerrit_integration_context.project_verified
    open_rows = open_changes_on_branch(gerrit_admin_session, proj, topic)
    assert len(open_rows) == 2
    tip = git_out("rev-parse", "HEAD", cwd=repo).strip()
    pushed_tip = git_out("rev-parse", "HEAD~2", cwd=repo).strip()
    assert git_out("rev-parse", f"lastPush/{topic}", cwd=repo).strip() == pushed_tip
    assert tip != pushed_tip


def test_push_all_ignores_stop_pattern(
    tmp_path,
    gerrit_integration_context,
    gerrit_admin_session: GerritHttpSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    topic = f"all_{secrets.token_hex(4)}"
    repo = prepare_topic_repo(gerrit_integration_context, tmp_path, topic)
    build_linear_chain(
        repo,
        [
            "feat: first commit",
            "feat: second commit",
            "wip: blocks here",
            "feat: fourth commit",
        ],
    )
    code, _out, err = run_cli(
        repo,
        ger_push_main,
        ["--yes", "--all", "--no-rebase-check"],
        monkeypatch,
    )
    assert code == 0, err
    proj = gerrit_integration_context.project_verified
    open_rows = open_changes_on_branch(gerrit_admin_session, proj, topic)
    assert len(open_rows) == 4


def test_push_hotfix_branch_is_isolated(
    tmp_path,
    gerrit_integration_context,
    gerrit_admin_session: GerritHttpSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Commits pushed from ``hotfix_123`` do not appear on a topic branch and vice versa."""
    from tests.integration.integration_helpers import prepare_clone_at_branch

    ctx = gerrit_integration_context
    proj = ctx.project_verified
    topic = f"iso_{secrets.token_hex(4)}"

    topic_repo = prepare_topic_repo(ctx, tmp_path / "a", topic)
    build_linear_chain(topic_repo, ["topic only"])
    code, _out, err = run_cli(
        topic_repo,
        ger_push_main,
        ["--yes", "--no-rebase-check"],
        monkeypatch,
    )
    assert code == 0, err
    assert len(open_changes_on_branch(gerrit_admin_session, proj, topic)) == 1

    hf_dir = prepare_clone_at_branch(ctx, tmp_path / "b", "hotfix_123", "hf")
    build_linear_chain(hf_dir, ["hotfix branch work"])
    code2, _o2, e2 = run_cli(
        hf_dir,
        ger_push_main,
        ["--yes", "--no-rebase-check"],
        monkeypatch,
    )
    assert code2 == 0, e2
    assert len(open_changes_on_branch(gerrit_admin_session, proj, "hotfix_123")) >= 1

    n_topic = len(open_changes_on_branch(gerrit_admin_session, proj, topic))
    assert n_topic == 1
