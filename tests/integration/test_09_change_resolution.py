"""Change resolution against a live Gerrit (integration).

Requires Docker Gerrit — see ``tests/integration/README.md``.

Covers cross-command resolution consistency, branch-aware narrowing, live
``ger log --json`` overlay (including compact ``project~number`` ids), and
batch query call budgets (project-scoped Change-Id OR, not per-change).
"""

from __future__ import annotations

import json
import math
import secrets
from typing import Any
from unittest.mock import patch

import pytest

from gerrit_workflow_tools.cli_log import main as ger_log_main
from gerrit_workflow_tools.cli_push import main as ger_push_main
from gerrit_workflow_tools.cli_resolve import main as ger_resolve_main
from gerrit_workflow_tools.cli_show import main as ger_show_main
from gerrit_workflow_tools.core.gerrit.change_resolution import resolve_stack_context
from gerrit_workflow_tools.core.gerrit.rest import (
    _BATCH_OR_CHUNK,
    HttpGerritRest,
    resolve_gerrit_web_base,
)
from gerrit_workflow_tools.core.git_run import git, git_out
from tests.cli_gerrit_mocks import head_change_id
from tests.conftest import run_cli
from tests.integration.gerrit_http import GerritHttpSession
from tests.integration.integration_helpers import (
    open_changes_on_branch,
    prepare_clone_at_branch,
    prepare_topic_repo,
)
from tests.integration.repo_builder import build_linear_chain, install_commit_msg_hook


def _configure_gerrit_project(repo, project: str) -> None:
    git("config", "gerrit.project", project, cwd=repo)


def _clear_gerrit_cache(repo) -> None:
    from gerrit_workflow_tools.core.gerrit.cache import GerritCache

    web = resolve_gerrit_web_base(repo)
    GerritCache.for_web_base(web).clear()


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
    """Same Change-Id on main and dev: resolve/show JSON agree; log overlays target branch."""
    ctx = gerrit_integration_context
    proj = ctx.project_verified
    main_repo, cid, dev_ctx = _push_shared_change_to_main_and_dev(ctx, tmp_path, monkeypatch)

    main_change = _change_on_branch(gerrit_admin_session, proj, "main", cid)
    dev_change = _change_on_branch(gerrit_admin_session, proj, "dev", cid)
    assert main_change["_number"] != dev_change["_number"]

    stack = resolve_stack_context(main_repo)
    assert stack.push_branch == "main"

    _clear_gerrit_cache(main_repo)
    code_log, out_log, elog = run_cli(main_repo, ger_log_main, ["--json", "--color", "never"], monkeypatch)
    assert code_log in (0, 1), elog
    log_commits = json.loads(out_log)["commits"]
    main_row = next(c for c in log_commits if c.get("change_id") == cid)
    assert main_row["pushed"] is True
    assert f"/+/{main_change['_number']}" in (main_row.get("gerrit_url") or "")
    assert f"/+/{dev_change['_number']}" not in (main_row.get("gerrit_url") or "")

    code_show, out_show, eshow = run_cli(main_repo, ger_show_main, ["--json", cid], monkeypatch)
    code_resolve, out_resolve, eresolve = run_cli(main_repo, ger_resolve_main, ["--json", cid], monkeypatch)
    assert code_show in (0, 1), eshow
    assert code_resolve == 0, eresolve
    show_resolution = json.loads(out_show)["resolution"]
    resolve_resolution = json.loads(out_resolve)["resolution"]
    assert show_resolution == resolve_resolution
    assert show_resolution["selected"]["branch"] == "main"
    assert show_resolution["selected"]["number"] == main_change["_number"]
    assert show_resolution["selected_reason"] == "target-branch"
    assert show_resolution["ambiguous"] is True

    # Compact project~number ids still alias to the selected change.
    selected_triplet = show_resolution["selected"]["triplet"]
    assert "~" in selected_triplet
    assert cid in selected_triplet or selected_triplet == str(main_change.get("id"))

    dev_repo = dev_ctx["repo"]
    git("checkout", "feat_dev", cwd=dev_repo)
    _clear_gerrit_cache(dev_repo)
    code_log_dev, out_log_dev, elog_dev = run_cli(dev_repo, ger_log_main, ["--json", "--color", "never"], monkeypatch)
    assert code_log_dev in (0, 1), elog_dev
    dev_commits = json.loads(out_log_dev)["commits"]
    dev_row = next(c for c in dev_commits if c.get("change_id") == cid)
    assert dev_row["pushed"] is True
    assert f"/+/{dev_change['_number']}" in (dev_row.get("gerrit_url") or "")
    assert f"/+/{main_change['_number']}" not in (dev_row.get("gerrit_url") or "")


def test_push_then_resolve_triplet_consistency(
    tmp_path,
    gerrit_integration_context,
    gerrit_admin_session: GerritHttpSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ger push`` then ``ger resolve`` / ``ger show`` agree on the same resolution block."""
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


def test_ger_log_batch_query_budget_with_unpublished(
    tmp_path,
    gerrit_integration_context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stack overlay must use O(chunks) project-scoped OR queries, not per Change-Id."""
    topic = f"batch_{secrets.token_hex(4)}"
    repo = prepare_topic_repo(gerrit_integration_context, tmp_path, topic)
    _configure_gerrit_project(repo, gerrit_integration_context.project_verified)

    n_pushed = _BATCH_OR_CHUNK + 5
    messages = [f"batch commit {i}" for i in range(n_pushed)]
    build_linear_chain(repo, messages)
    code, _out, err = run_cli(
        repo,
        ger_push_main,
        ["--yes", "--no-rebase-check"],
        monkeypatch,
    )
    assert code == 0, err

    # Local-only Change-Ids that Gerrit has never seen (empty batch must not N+1).
    build_linear_chain(repo, ["unpublished a", "unpublished b", "unpublished c"])
    total_with_ids = n_pushed + 3
    expected_chunks = math.ceil(total_with_ids / _BATCH_OR_CHUNK)

    _clear_gerrit_cache(repo)
    queries: list[str] = []
    original = HttpGerritRest.query_changes

    def _counting_query_changes(
        self: HttpGerritRest,
        q: str,
        *,
        n: int = 25,
        options: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        queries.append(q)
        return original(self, q, n=n, options=options)

    with patch.object(HttpGerritRest, "query_changes", _counting_query_changes):
        code_log, out_log, elog = run_cli(repo, ger_log_main, ["--json", "--color", "never"], monkeypatch)
    assert code_log in (0, 1), elog
    commits = json.loads(out_log)["commits"]
    assert len([c for c in commits if c.get("change_id")]) >= total_with_ids

    batch_qs = [q for q in queries if "project:" in q and "change:" in q]
    # Cold cache: one detail fetch pass (no probe). May be 1-2 chunks.
    assert len(batch_qs) <= expected_chunks + 1, (
        f"expected ~{expected_chunks} batch queries, got {len(batch_qs)}: {batch_qs!r}"
    )
    assert len(batch_qs) >= 1
    for q in batch_qs:
        assert "branch:" not in q, f"batch query must not scope by branch: {q!r}"
    # Empty unpublished must not fall back to per-ref triplet queries.
    per_change = [q for q in queries if "branch:" in q and q.count("change:") == 1]
    assert per_change == [], f"unexpected per-change fallbacks: {per_change!r}"
