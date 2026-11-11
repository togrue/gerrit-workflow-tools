"""Integration: ``ger checkout`` against a real Gerrit.

One clone pushes a chain with ``ger push``; another clone checks a change of it out, works on
top of it, and picks up a new patchset the author pushes (update declined, then accepted). A
Change-Id that lives on ``main`` and ``dev`` is narrowed, named exactly, or refused as
ambiguous. ``ger`` runs as the dev user.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

import pytest

from gerrit_workflow_tools.cli_checkout import main as ger_checkout_main
from gerrit_workflow_tools.cli_push import main as ger_push_main
from gerrit_workflow_tools.core.git_run import git, git_out
from gerrit_workflow_tools.core.stack import get_stack_snapshot
from tests.cli_gerrit_mocks import head_change_id
from tests.conftest import run_cli
from tests.helpers import force_zero_change_trust_window
from tests.integration.conftest import GerritIntegrationContext
from tests.integration.gerrit_http import GerritHttpSession, quote_change_id
from tests.integration.integration_helpers import open_changes_on_branch, prepare_clone_at_branch, prepare_topic_repo
from tests.integration.repo_builder import build_linear_chain


def _checkout(cwd: Path, argv: list[str], monkeypatch: pytest.MonkeyPatch) -> tuple[int, str, str]:
    return run_cli(cwd, ger_checkout_main, argv, monkeypatch, catch_sys_exit=True)


def _current_revision(session: GerritHttpSession, change_id: str) -> str:
    detail = session.get_json(f"changes/{quote_change_id(change_id)}", params=[("o", "CURRENT_REVISION")])
    return str(detail["current_revision"])


def _on(repo: Path) -> tuple[str, str]:
    return git_out("branch", "--show-current", cwd=repo), git_out("rev-parse", "HEAD", cwd=repo)


def test_checkout_a_pushed_change_then_pick_up_its_new_patchset(
    tmp_path: Path,
    gerrit_integration_context: GerritIntegrationContext,
    gerrit_admin_session: GerritHttpSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = gerrit_integration_context
    force_zero_change_trust_window(monkeypatch)
    topic = f"checkout_{secrets.token_hex(5)}"
    author = prepare_topic_repo(ctx, tmp_path, topic)
    build_linear_chain(author, ["checkout base", "checkout top"])
    code, _out, err = run_cli(author, ger_push_main, ["--yes", "--no-rebase-check"], monkeypatch)
    assert code == 0, err
    base, top = sorted(
        open_changes_on_branch(gerrit_admin_session, ctx.project_verified, topic), key=lambda r: r["_number"]
    )
    number = int(top["_number"])

    # Another clone checks the top change out: its own branch, tracking the change's target branch.
    taker = prepare_clone_at_branch(ctx, tmp_path, "main", "taker")
    code, out, err = _checkout(taker, [str(number)], monkeypatch)
    assert code == 0, err
    assert f"on change-{number}" in out and f"upstream: origin/{topic}" in out
    assert _on(taker) == (f"change-{number}", _current_revision(gerrit_admin_session, top["change_id"]))
    assert git_out("rev-parse", "--abbrev-ref", "@{upstream}", cwd=taker) == f"origin/{topic}"
    stack = get_stack_snapshot(taker).commits
    assert [c.change_id for c in stack] == [base["change_id"], top["change_id"]]

    # Work stacked on it, then the author uploads patchset 2 of the top change.
    (taker / "follow_up.txt").write_text("mine\n", encoding="utf-8")
    git("add", "follow_up.txt", cwd=taker)
    git("commit", "-q", "-m", "my follow-up", cwd=taker)
    (author / "chain_1.txt").write_text("checkout top, reworded\n", encoding="utf-8")
    git("commit", "-q", "-a", "--amend", "--no-edit", cwd=author)
    git("push", "-q", "origin", f"HEAD:refs/for/{topic}", cwd=author)
    ps2 = _current_revision(gerrit_admin_session, top["change_id"])

    # Without a terminal the update is not guessed; --no-update leaves the branch alone.
    tip = git_out("rev-parse", "HEAD", cwd=taker)
    code, _out, err = _checkout(taker, [str(number)], monkeypatch)
    assert code == 1 and "--update" in err and "--no-update" in err
    code, out, err = _checkout(taker, [str(number), "--no-update"], monkeypatch)
    assert code == 0 and "left at patchset 1 (Gerrit has patchset 2)" in out, err
    assert _on(taker) == (f"change-{number}", tip)

    # --update moves the change to patchset 2 and rebases the follow-up onto it.
    code, out, err = _checkout(taker, [str(number), "--update"], monkeypatch)
    assert code == 0, err
    assert "from patchset 1 to" in out and "rebased the 1 commit(s) above it" in out
    assert git_out("rev-parse", "HEAD~1", cwd=taker) == ps2
    assert git_out("log", "-1", "--format=%s", cwd=taker) == "my follow-up"


def _change_on(session: GerritHttpSession, project: str, branch: str, change_id: str) -> dict[str, Any]:
    rows = session.get_json("changes/", params=[("q", f"project:{project} branch:{branch} change:{change_id}")])
    assert isinstance(rows, list) and len(rows) == 1, rows
    return dict(rows[0])


def test_same_change_id_on_two_target_branches(
    tmp_path: Path,
    gerrit_integration_context: GerritIntegrationContext,
    gerrit_admin_session: GerritHttpSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = gerrit_integration_context
    proj = ctx.project_verified
    force_zero_change_trust_window(monkeypatch)

    # One commit pushed to main, and cherry-picked to dev: one Change-Id, two changes.
    author = prepare_clone_at_branch(ctx, tmp_path, "main", "co_author")
    git("checkout", "-q", "-b", "co_main", "origin/main", cwd=author)
    build_linear_chain(author, [f"checkout backport {secrets.token_hex(4)}"])
    cid, sha = head_change_id(author), git_out("rev-parse", "HEAD", cwd=author)
    assert run_cli(author, ger_push_main, ["--yes", "--no-rebase-check"], monkeypatch)[0] == 0
    git("checkout", "-q", "-b", "co_dev", "origin/dev", cwd=author)
    git("cherry-pick", sha, cwd=author)
    assert run_cli(author, ger_push_main, ["--yes", "--no-rebase-check"], monkeypatch)[0] == 0
    on_main, on_dev = (
        _change_on(gerrit_admin_session, proj, "main", cid),
        _change_on(gerrit_admin_session, proj, "dev", cid),
    )

    # From a branch pushing to neither, the bare Change-Id is ambiguous; both are listed.
    side = prepare_topic_repo(ctx, tmp_path, f"checkout_side_{secrets.token_hex(4)}")
    code, _out, err = _checkout(side, [cid], monkeypatch)
    assert code == 4, err
    assert f"change:{on_main['_number']} (" in err and f"change:{on_dev['_number']} (" in err

    # From main, narrowing picks main's change; the triplet picks dev's, beside it.
    taker = prepare_clone_at_branch(ctx, tmp_path, "main", "co_taker")
    code, out, err = _checkout(taker, [cid], monkeypatch)
    assert code == 0, err
    assert f"on change-{on_main['_number']}" in out and "upstream: origin/main" in out
    code, out, err = _checkout(taker, [f"{proj}~dev~{cid}"], monkeypatch)
    assert code == 0, err
    # main's checkout carries the Change-Id too, but pushes to main: a cherry-pick, not a local version.
    assert f"on change-{on_dev['_number']}" in out and "upstream: origin/dev" in out
    assert _on(taker)[1] == _current_revision(gerrit_admin_session, f"{proj}~dev~{cid}")
