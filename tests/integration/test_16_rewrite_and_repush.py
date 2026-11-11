"""Integration: rewriting a pushed stack, then pushing it again.

``ger fix``, ``ger reword`` and ``ger edit`` keep each commit's Change-Id, so the next
``ger push`` updates the same changes. Gerrit makes a new patchset only for a commit whose SHA
changed: the touched commit and those above it. Pushing up to a revision, re-pushing an
unchanged stack, and pushing a reordered stack are covered too. Patchset numbers are
Gerrit's, read back over REST.
"""

from __future__ import annotations

import secrets
from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_edit import main as ger_edit_main, main_reword as ger_reword_main
from gerrit_workflow_tools.cli_fix import main as ger_fix_main
from gerrit_workflow_tools.core.git_run import git, git_out
from tests.integration.conftest import GerritIntegrationContext
from tests.integration.gerrit_http import GerritHttpSession
from tests.integration.integration_helpers import (
    ChainCommit,
    chain_commits_oldest_first,
    change_detail,
    current_patchset,
    ger_push,
    log_commits_by_change_id,
    open_changes_on_branch,
    prepare_topic_repo,
    run_ger,
)
from tests.integration.repo_builder import build_linear_chain


def _stack(ctx: GerritIntegrationContext, tmp_path: Path, name: str, size: int) -> tuple[Path, str]:
    branch = f"{name}_{secrets.token_hex(4)}"
    repo = prepare_topic_repo(ctx, tmp_path, branch)
    build_linear_chain(repo, [f"{name} {i}" for i in range(size)])
    return repo, branch


def _patchsets(session: GerritHttpSession, project: str, branch: str, chain: list[ChainCommit]) -> list[int]:
    return [current_patchset(session, f"{project}~{branch}~{c.change_id}") for c in chain]


def test_push_up_to_a_revision_then_the_rest_then_nothing(
    tmp_path: Path,
    gerrit_integration_context: GerritIntegrationContext,
    gerrit_admin_session: GerritHttpSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = gerrit_integration_context
    repo, branch = _stack(ctx, tmp_path, "partial", 3)
    ger_push(repo, monkeypatch, "HEAD~1")
    assert len(open_changes_on_branch(gerrit_admin_session, ctx.project_verified, branch)) == 2

    ger_push(repo, monkeypatch)
    chain = chain_commits_oldest_first(repo)
    assert _patchsets(gerrit_admin_session, ctx.project_verified, branch, chain) == [1, 1, 1]

    out, _err = ger_push(repo, monkeypatch)
    assert "nothing to push: Gerrit already has these commits" in out
    assert _patchsets(gerrit_admin_session, ctx.project_verified, branch, chain) == [1, 1, 1]


def test_fix_reword_and_edit_then_push_renew_only_the_touched_change_and_those_above(
    tmp_path: Path,
    gerrit_integration_context: GerritIntegrationContext,
    gerrit_admin_session: GerritHttpSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = gerrit_integration_context
    proj = ctx.project_verified
    repo, branch = _stack(ctx, tmp_path, "rewrite", 4)
    ger_push(repo, monkeypatch)
    chain = chain_commits_oldest_first(repo)
    change_ids = [c.change_id for c in chain]
    upstream = git_out("rev-parse", "--abbrev-ref", "@{upstream}", cwd=repo)

    def _push_and_read() -> list[int]:
        ger_push(repo, monkeypatch)
        assert [c.change_id for c in chain_commits_oldest_first(repo)] == change_ids
        return _patchsets(gerrit_admin_session, proj, branch, chain)

    # ger fix by Change-Id, squashed: commit 1 and the two above it move.
    (repo / "chain_1.txt").write_text("fixed\n", encoding="utf-8")
    git("add", "chain_1.txt", cwd=repo)
    code, _out, err = run_ger(repo, ger_fix_main, [chain[1].change_id], monkeypatch)
    assert code == 0, err
    git("-c", "sequence.editor=:", "rebase", "-q", "-i", "--autosquash", upstream, cwd=repo)
    assert _push_and_read() == [1, 2, 2, 2]

    # ger reword by change number: commit 2 and the one above it.
    number = change_detail(gerrit_admin_session, f"{proj}~{branch}~{chain[2].change_id}")["_number"]
    monkeypatch.setenv("GIT_EDITOR", "sed -i -e '1s/$/ (reworded)/'")
    code, _out, err = run_ger(repo, ger_reword_main, [f"change:{number}"], monkeypatch)
    assert code == 0, err
    assert git_out("log", "-1", "--format=%s", "HEAD~1", cwd=repo) == "rewrite 2 (reworded)"
    assert _push_and_read() == [1, 2, 3, 3]

    # ger edit by triplet stops at the tip; amend and continue: only the tip moves.
    code, _out, err = run_ger(repo, ger_edit_main, [f"{proj}~{branch}~{chain[3].change_id}"], monkeypatch)
    assert code == 0, err
    (repo / "chain_3.txt").write_text("edited\n", encoding="utf-8")
    git("commit", "-q", "-a", "--amend", "--no-edit", cwd=repo)
    monkeypatch.setenv("GIT_EDITOR", "true")
    git("rebase", "--continue", cwd=repo)
    assert _push_and_read() == [1, 2, 3, 4]


def test_a_reordered_stack_pushes_as_new_patchsets_of_the_same_changes(
    tmp_path: Path,
    gerrit_integration_context: GerritIntegrationContext,
    gerrit_admin_session: GerritHttpSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = gerrit_integration_context
    repo, branch = _stack(ctx, tmp_path, "reorder", 3)
    ger_push(repo, monkeypatch)
    c0, c1, c2 = chain_commits_oldest_first(repo)

    # Swap the top two (they touch different files, so the picks apply cleanly).
    git("reset", "-q", "--hard", c0.sha, cwd=repo)
    git("cherry-pick", c2.sha, c1.sha, cwd=repo)
    ger_push(repo, monkeypatch)

    assert len(open_changes_on_branch(gerrit_admin_session, ctx.project_verified, branch)) == 3
    reordered = chain_commits_oldest_first(repo)
    assert [c.change_id for c in reordered] == [c0.change_id, c2.change_id, c1.change_id]
    assert _patchsets(gerrit_admin_session, ctx.project_verified, branch, reordered) == [1, 2, 2]
    # Gerrit's relation chain follows the new order: c1's new patchset sits on c2's.
    detail = change_detail(gerrit_admin_session, f"{ctx.project_verified}~{branch}~{c1.change_id}")
    revision = gerrit_admin_session.get_json(f"changes/{detail['id']}/revisions/{detail['current_revision']}/commit")
    assert [p["commit"] for p in revision["parents"]] == [reordered[1].sha]
    rows = log_commits_by_change_id(repo, monkeypatch)
    assert {row["patchset_status"] for row in rows.values()} == {"active"}
