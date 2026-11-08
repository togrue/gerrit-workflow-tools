"""Integration: ``ger review`` and ``ger comment`` against a real Gerrit.

A two-change chain is pushed with ``ger push``, then reviewed from the same clone: the
change is fetched by its ``refs/changes/…`` ref into the review worktree, staged, walked
with ``--next``/``--prev``, commented on (a Gerrit draft and a local note), and voted on.
``ger`` runs as the dev user; the admin session checks what reviewers see.
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

import pytest

from gerrit_workflow_tools.cli_comment import main as ger_comment_main
from gerrit_workflow_tools.cli_push import main as ger_push_main
from gerrit_workflow_tools.cli_review import main as ger_review_main
from gerrit_workflow_tools.core.git_run import git_out
from gerrit_workflow_tools.core.local_comments import LocalCommentStore
from tests.conftest import run_cli
from tests.helpers import force_zero_change_trust_window
from tests.integration.gerrit_http import GerritHttpSession, quote_change_id
from tests.integration.integration_helpers import label_value, open_changes_on_branch, prepare_topic_repo
from tests.integration.repo_builder import build_linear_chain


def _pushed_chain(
    tmp_path: Path, ctx: Any, admin: GerritHttpSession, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, list[dict[str, Any]]]:
    """Two pushed changes in one chain; returns the clone and the changes, base first."""
    force_zero_change_trust_window(monkeypatch)
    topic = f"review_{secrets.token_hex(5)}"
    repo = prepare_topic_repo(ctx, tmp_path, topic)
    build_linear_chain(repo, ["review base", "review top"])
    code, _out, err = run_cli(repo, ger_push_main, ["--yes", "--no-rebase-check"], monkeypatch)
    assert code == 0, err
    rows = open_changes_on_branch(admin, ctx.project_verified, topic)
    assert len(rows) == 2, rows
    return repo, sorted(rows, key=lambda row: int(row["_number"]))


def _review(cwd: Path, argv: list[str], monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    code, out, err = run_cli(cwd, ger_review_main, [*argv, "--json"], monkeypatch)
    assert code == 0, err
    return json.loads(out)


def _drafts(session: GerritHttpSession, change_id: str) -> list[dict[str, Any]]:
    file_map = session.get_json(f"changes/{quote_change_id(change_id)}/drafts")
    return [{**row, "path": path} for path, rows in file_map.items() for row in rows]


def _published(session: GerritHttpSession, change_id: str) -> list[dict[str, Any]]:
    file_map = session.get_json(f"changes/{quote_change_id(change_id)}/comments")
    return [{**row, "path": path} for path, rows in file_map.items() for row in rows]


def test_review_stages_walks_comments_and_votes(
    tmp_path: Path,
    gerrit_integration_context: Any,
    gerrit_admin_session: GerritHttpSession,
    gerrit_dev_session: GerritHttpSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, (base, top) = _pushed_chain(tmp_path, gerrit_integration_context, gerrit_admin_session, monkeypatch)
    base_number, top_number = int(base["_number"]), int(top["_number"])
    own_head = git_out("rev-parse", "HEAD", cwd=repo)

    # Open the base change: fetched from refs/changes, staged in the review worktree.
    state = _review(repo, ["--in-worktree", str(base_number)], monkeypatch)
    worktree = Path(state["worktree"])
    assert (state["number"], state["patch_set"], state["staged"]) == (base_number, 1, True)
    assert state["chain"] == [base_number, top_number]
    assert git_out("diff", "--cached", "--name-only", cwd=worktree) == "chain_0.txt"
    assert git_out("rev-parse", "HEAD", cwd=repo) == own_head

    # A Gerrit draft on the reviewed patchset, and a private note beside it.
    code, out, err = run_cli(worktree, ger_comment_main, ["chain_0.txt:1", "-m", "nit: wording", "--json"], monkeypatch)
    assert code == 0, err
    draft = json.loads(out)
    assert (draft["state"], draft["patch_set"]) == ("draft", 1)
    (on_gerrit,) = _drafts(gerrit_dev_session, base["change_id"])
    assert (on_gerrit["path"], on_gerrit["line"], on_gerrit["message"]) == ("chain_0.txt", 1, "nit: wording")
    assert _published(gerrit_admin_session, base["change_id"]) == []

    code, out, err = run_cli(worktree, ger_comment_main, ["chain_0.txt:1", "-m", "ask later", "--note"], monkeypatch)
    assert code == 0, err
    assert [c.message for c in LocalCommentStore(repo).all()] == ["ask later"]

    # Walk up the chain and back.
    assert _review(worktree, ["--next"], monkeypatch)["number"] == top_number
    assert git_out("diff", "--cached", "--name-only", cwd=worktree) == "chain_1.txt"
    code, _out, err = run_cli(worktree, ger_review_main, ["--next"], monkeypatch)
    assert code == 10, err
    assert _review(worktree, ["--prev"], monkeypatch)["number"] == base_number

    # Vote: the draft is published with the vote; the note stays local.
    code, out, err = run_cli(worktree, ger_review_main, ["--vote", "CR+1", "-y", "--json"], monkeypatch)
    assert code == 0, err
    assert json.loads(out)["drafts"] == 1
    assert _drafts(gerrit_dev_session, base["change_id"]) == []
    (published,) = _published(gerrit_admin_session, base["change_id"])
    assert published["message"] == "nit: wording"
    detail = gerrit_admin_session.get_json(f"changes/{quote_change_id(base['change_id'])}/detail")
    assert label_value(detail, "Code-Review") == 1
    assert [c.message for c in LocalCommentStore(repo).all()] == ["ask later"]

    # Done: branches gone, your own checkout as it was.
    done = _review(repo, ["--done"], monkeypatch)
    assert sorted(done["deleted"]) == sorted([f"change-{base_number}", f"change-{top_number}"])
    assert git_out("branch", "--list", "change-*", cwd=repo) == ""
    assert git_out("rev-parse", "HEAD", cwd=repo) == own_head


def test_vote_waits_for_a_newer_patchset_to_be_reviewed(
    tmp_path: Path,
    gerrit_integration_context: Any,
    gerrit_admin_session: GerritHttpSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, (base, _top) = _pushed_chain(tmp_path, gerrit_integration_context, gerrit_admin_session, monkeypatch)
    _review(repo, ["--in-worktree", str(base["_number"])], monkeypatch)

    # The owner uploads patchset 2 of the base change (amend + push from the own clone).
    git_out("checkout", "-q", "HEAD~1", cwd=repo)
    (repo / "chain_0.txt").write_text("reworded\n", encoding="utf-8")
    git_out("commit", "-q", "-a", "--amend", "--no-edit", cwd=repo)
    git_out("push", "-q", "origin", f"HEAD:refs/for/{base['branch']}", cwd=repo)

    code, _out, err = run_cli(repo, ger_review_main, ["--vote", "CR+1", "-y"], monkeypatch)
    assert code == 1 and "you reviewed 1" in err
    state = _review(repo, [str(base["_number"])], monkeypatch)
    assert state["patch_set"] == 2
    run_cli(repo, ger_review_main, ["--done"], monkeypatch)
