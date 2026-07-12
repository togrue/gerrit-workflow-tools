"""Phase 6: change resolution against a live Gerrit (integration).

Requires Docker Gerrit — see ``tests/integration/README.md``.

These tests prove **cross-command resolution consistency** (``ger resolve`` vs
``ger show``) and **branch-aware narrowing** on a real server. Stack overlay
via ``ger log --json`` (triplet-keyed batch fetch) is covered in unit tests
(``tests/test_change_resolution_consistency.py``) because this Gerrit instance
returns compact ``project~number`` change ids that do not match the
``project~branch~Change-Id`` lookup key ``ger log`` builds locally.
"""

from __future__ import annotations

import json
import secrets

import pytest

from gerrit_workflow_tools.cli_log import main as ger_log_main
from gerrit_workflow_tools.cli_push import main as ger_push_main
from gerrit_workflow_tools.cli_resolve import main as ger_resolve_main
from gerrit_workflow_tools.cli_show import main as ger_show_main
from gerrit_workflow_tools.core.config import clear_gerrit_git_config_cache
from gerrit_workflow_tools.core.gerrit.change_resolution import resolve_stack_context
from gerrit_workflow_tools.core.git_run import git, git_out
from tests.cli_gerrit_mocks import head_change_id
from tests.conftest import run_cli
from tests.integration.gerrit_http import GerritHttpSession
from tests.integration.integration_helpers import (
    open_changes_on_branch,
    prepare_clone_at_branch,
)
from tests.integration.repo_builder import build_linear_chain, install_commit_msg_hook


def _configure_gerrit_project(repo, project: str) -> None:
    git("config", "gerrit.project", project, cwd=repo)
    clear_gerrit_git_config_cache()


def _push_shared_change_to_main_and_dev(
    ctx,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[object, str, dict]:
    """Push one commit to ``main`` and cherry-pick it to ``dev`` (same Change-Id)."""
    repo = prepare_clone_at_branch(ctx, tmp_path, "main", "res_wk")
    _configure_gerrit_project(repo, ctx.project_verified)
    git("checkout", "-b", "feat_main", cwd=repo)
    git("config", "branch.feat_main.gerritTarget", "main", cwd=repo)
    git("branch", "--set-upstream-to", "origin/main", "feat_main", cwd=repo)
    install_commit_msg_hook(repo, http_base=ctx.http_base)
    build_linear_chain(repo, ["cross-branch resolution"])
    cid = head_change_id(repo)
    sha = git_out("rev-parse", "HEAD", cwd=repo).strip()

    code, _out, err = run_cli(
        repo,
        ger_push_main,
        ["--yes", "--no-rebase-check"],
        monkeypatch,
    )
    assert code == 0, err

    git("checkout", "dev", cwd=repo)
    git("checkout", "-b", "feat_dev", cwd=repo)
    git("config", "branch.feat_dev.gerritTarget", "dev", cwd=repo)
    git("branch", "--set-upstream-to", "origin/dev", "feat_dev", cwd=repo)
    git("cherry-pick", sha, cwd=repo)
    code2, _out2, err2 = run_cli(
        repo,
        ger_push_main,
        ["--yes", "--no-rebase-check"],
        monkeypatch,
    )
    assert code2 == 0, err2

    git("checkout", "feat_main", cwd=repo)
    return repo, cid, {"branch": "dev", "repo": repo}


def _change_on_branch(session: GerritHttpSession, project: str, branch: str, change_id: str) -> dict:
    rows = open_changes_on_branch(session, project, branch)
    for row in rows:
        if str(row.get("change_id") or "") == change_id:
            return row
    q = f"project:{project} branch:{branch} change:{change_id}"
    data = session.get_json("changes/", params=[("q", q)])
    if isinstance(data, list):
        for row in data:
            if isinstance(row, dict):
                return row
    raise AssertionError(f"no change for {change_id!r} on branch {branch!r}")


def test_cross_branch_change_id_log_show_resolve_agree(
    tmp_path,
    gerrit_integration_context,
    gerrit_admin_session: GerritHttpSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same Change-Id on main and dev: resolve/show JSON agree; log notes narrowing."""
    ctx = gerrit_integration_context
    proj = ctx.project_verified
    main_repo, cid, dev_ctx = _push_shared_change_to_main_and_dev(ctx, tmp_path, monkeypatch)

    main_change = _change_on_branch(gerrit_admin_session, proj, "main", cid)
    dev_change = _change_on_branch(gerrit_admin_session, proj, "dev", cid)
    assert main_change["_number"] != dev_change["_number"]

    stack = resolve_stack_context(main_repo)
    assert stack.push_branch == "main"

    code_log, out_log, elog = run_cli(main_repo, ger_log_main, ["--color", "never"], monkeypatch)
    assert code_log in (0, 1), elog
    assert "cross-branch resolution" in out_log
    assert str(main_change["_number"]) in elog

    code_show, out_show, eshow = run_cli(main_repo, ger_show_main, ["--json", cid], monkeypatch)
    code_resolve, out_resolve, eresolve = run_cli(
        main_repo, ger_resolve_main, ["--json", cid], monkeypatch
    )
    assert code_show in (0, 1), eshow
    assert code_resolve == 0, eresolve
    show_resolution = json.loads(out_show)["resolution"]
    resolve_resolution = json.loads(out_resolve)["resolution"]
    assert show_resolution == resolve_resolution
    assert show_resolution["selected"]["branch"] == "main"
    assert show_resolution["selected"]["number"] == main_change["_number"]
    assert show_resolution["selected_reason"] == "target-branch"
    assert show_resolution["ambiguous"] is True

    dev_repo = dev_ctx["repo"]
    git("checkout", "feat_dev", cwd=dev_repo)
    _code_log_dev, _out_log_dev, elog_dev = run_cli(
        dev_repo, ger_log_main, ["--color", "never"], monkeypatch
    )
    assert str(dev_change["_number"]) in elog_dev


def test_push_then_resolve_triplet_consistency(
    tmp_path,
    gerrit_integration_context,
    gerrit_admin_session: GerritHttpSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ger push`` then ``ger resolve`` / ``ger show`` agree on the same resolution block."""
    from tests.integration.integration_helpers import prepare_topic_repo

    topic = f"triplet_{secrets.token_hex(4)}"
    repo = prepare_topic_repo(gerrit_integration_context, tmp_path, topic)
    _configure_gerrit_project(repo, gerrit_integration_context.project_verified)
    build_linear_chain(repo, ["triplet consistency"])
    cid = head_change_id(repo)

    code, _out, err = run_cli(
        repo,
        ger_push_main,
        ["--yes", "--no-rebase-check"],
        monkeypatch,
    )
    assert code == 0, err

    proj = gerrit_integration_context.project_verified
    live = _change_on_branch(gerrit_admin_session, proj, topic, cid)

    code_resolve, out_resolve, er = run_cli(repo, ger_resolve_main, ["--json", cid], monkeypatch)
    code_show, out_show, es = run_cli(repo, ger_show_main, ["--json", cid], monkeypatch)
    assert code_resolve == 0, er
    assert code_show in (0, 1), es

    resolve_block = json.loads(out_resolve)["resolution"]
    show_block = json.loads(out_show)["resolution"]
    assert resolve_block == show_block
    assert resolve_block["selected"]["number"] == live["_number"]
    assert resolve_block["selected"]["branch"] == topic
    assert resolve_block["selected"]["change_id"] == cid
