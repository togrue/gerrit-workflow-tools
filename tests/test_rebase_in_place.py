"""``ger rebase`` without a base rebases the local stack where it sits (#93).

Moving the stack onto the upstream is asked for by naming it (``ger rebase origin/main``,
``--onto-remote``), so a clean-up never brings the upstream's conflicts with it. When the
upstream moved on, the in-place rebase says how to rebase onto it, as ``ger log`` does.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_rebase import main as rebase_main
from gerrit_workflow_tools.core.git_run import git, git_out
from tests.conftest import run_cli


def _upstream_moves_on(repo: Path) -> str:
    """One more commit on ``origin/main`` than the stack sits on; return the stack's base."""
    base = git_out("rev-parse", "origin/main", cwd=repo)
    tree = git_out("rev-parse", "origin/main^{tree}", cwd=repo)
    moved = git_out("commit-tree", tree, "-p", base, "-m", "upstream moved", cwd=repo)
    git("update-ref", "refs/remotes/origin/main", moved, cwd=repo)
    return base


def test_ger_rebase_leaves_the_stack_on_its_base_and_says_the_upstream_moved(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = _upstream_moves_on(stack_repo)
    head = git_out("rev-parse", "HEAD", cwd=stack_repo)
    monkeypatch.setenv("GIT_EDITOR", "true")
    code, _out, err = run_cli(stack_repo, rebase_main, [], monkeypatch)
    assert code == 0, err
    assert git_out("rev-parse", "HEAD", cwd=stack_repo) == head
    assert git_out("merge-base", "HEAD", "origin/main", cwd=stack_repo) == base
    assert "note: origin/main has 1 new commit; rebase onto it with `ger rebase origin/main`" in err


def test_naming_the_upstream_moves_the_stack_onto_it(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _upstream_moves_on(stack_repo)
    monkeypatch.setenv("GIT_EDITOR", "true")
    code, _out, err = run_cli(stack_repo, rebase_main, ["origin/main"], monkeypatch)
    assert code == 0, err
    assert git_out("merge-base", "HEAD", "origin/main", cwd=stack_repo) == git_out(
        "rev-parse", "origin/main", cwd=stack_repo
    )
    assert "new commit" not in err


def test_no_note_while_the_upstream_has_not_moved(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_EDITOR", "true")
    code, _out, err = run_cli(stack_repo, rebase_main, [], monkeypatch)
    assert code == 0, err
    assert "note:" not in err


def test_a_branch_behind_its_upstream_stays_where_it_is(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No commits of its own: a rebase in place must not fast-forward it onto the upstream."""
    git("reset", "-q", "--hard", "origin/main", cwd=stack_repo)
    _upstream_moves_on(stack_repo)
    head = git_out("rev-parse", "HEAD", cwd=stack_repo)
    monkeypatch.setenv("GIT_EDITOR", "true")
    code, _out, err = run_cli(stack_repo, rebase_main, [], monkeypatch)
    assert code == 0, err
    assert git_out("rev-parse", "HEAD", cwd=stack_repo) == head
    assert "origin/main has 1 new commit" in err
