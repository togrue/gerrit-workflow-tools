# Spec: docu/spec/commands/rebase.md
"""``ger rebase --continue`` / ``--abort`` / ``--skip``: going on with the rebase underway, as git does."""

from __future__ import annotations

from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_rebase import main as rebase_main
from gerrit_workflow_tools.core.git_run import git, git_out
from gerrit_workflow_tools.core.rebase_state import RebaseStop, rebase_in_progress, rebase_progress
from tests.change_store import ChangeStore
from tests.conftest import run_cli
from tests.test_rebasing_stack import _am_stopped, _move_upstream_over_c_txt, _stop_to_edit


def _rebase(repo: Path, argv: list[str], monkeypatch: pytest.MonkeyPatch) -> tuple[int, str]:
    code, _out, err = run_cli(repo, rebase_main, argv, monkeypatch, catch_sys_exit=True, gerrit=ChangeStore({}))
    return code, err


def _subjects(repo: Path, rev: str = "feature") -> list[str]:
    return git_out("log", "--format=%s", f"origin/main..{rev}", cwd=repo).splitlines()


def _stopped_on_c_txt(repo: Path) -> str:
    """Rebase onto an upstream that also writes c.txt: it stops on "test! temporary experiment"."""
    tip = git_out("rev-parse", "feature", cwd=repo)
    _move_upstream_over_c_txt(repo)
    git("rebase", "-q", "origin/main", cwd=repo, check=False)
    progress = rebase_progress(repo)
    assert progress is not None and progress.stop is RebaseStop.CONFLICT
    return tip


def test_continue_at_an_edit_stop_finishes_the_rebase(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stop_to_edit(stack_repo)
    (stack_repo / "c.txt").write_text("amended\n", encoding="utf-8")
    git("commit", "-q", "-a", "--amend", "--no-edit", cwd=stack_repo)

    code, err = _rebase(stack_repo, ["--continue"], monkeypatch)

    assert code == 0, err
    assert not rebase_in_progress(stack_repo)
    assert git_out("show", "feature:c.txt", cwd=stack_repo) == "amended"


def test_continue_on_a_conflict_says_what_to_resolve_and_changes_nothing(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stopped_on_c_txt(stack_repo)
    head = git_out("rev-parse", "HEAD", cwd=stack_repo)

    code, err = _rebase(stack_repo, ["--continue"], monkeypatch)

    assert code == ExitCode.USAGE
    assert "the index has unresolved conflicts; resolve the conflicts in c.txt, `git add` them" in err
    assert rebase_in_progress(stack_repo) and git_out("rev-parse", "HEAD", cwd=stack_repo) == head


def test_continue_once_resolved_commits_the_resolution_without_an_editor(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No terminal: the commit message git would open an editor for is taken as it is."""
    _stopped_on_c_txt(stack_repo)
    (stack_repo / "c.txt").write_text("resolved\n", encoding="utf-8")
    git("add", "c.txt", cwd=stack_repo)

    code, err = _rebase(stack_repo, ["--continue"], monkeypatch)

    assert code == 0, err
    assert not rebase_in_progress(stack_repo)
    assert "test! temporary experiment" in _subjects(stack_repo)
    assert git_out("show", "feature:c.txt", cwd=stack_repo) == "resolved"


def test_skip_leaves_out_the_conflicting_commit(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    before = _subjects(stack_repo)
    _stopped_on_c_txt(stack_repo)

    code, err = _rebase(stack_repo, ["--skip"], monkeypatch)

    assert code == 0, err
    assert not rebase_in_progress(stack_repo)
    assert _subjects(stack_repo) == [s for s in before if s != "test! temporary experiment"]


def test_abort_puts_the_branch_back(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tip = _stopped_on_c_txt(stack_repo)

    code, err = _rebase(stack_repo, ["--abort"], monkeypatch)

    assert code == 0, err
    assert not rebase_in_progress(stack_repo)
    assert git_out("rev-parse", "HEAD", cwd=stack_repo) == tip
    assert git_out("symbolic-ref", "--short", "HEAD", cwd=stack_repo) == "feature"


def test_without_a_rebase_there_is_nothing_to_go_on_with(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for how in ("--continue", "--abort", "--skip"):
        code, err = _rebase(stack_repo, [how], monkeypatch)
        assert (code, err.strip()) == (ExitCode.USAGE, "error: no rebase is in progress")


def test_a_git_am_underway_is_named_not_continued(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _am_stopped(stack_repo)

    code, err = _rebase(stack_repo, ["--continue"], monkeypatch)

    assert code == ExitCode.USAGE
    assert (
        "no rebase is in progress: a `git am` is in progress; finish it (`git am --continue`) or `git am --abort`"
        in err
    )


def test_going_on_takes_no_rev_or_other_option(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stop_to_edit(stack_repo)
    for argv in (["--continue", "HEAD~1"], ["--skip", "--autosquash"], ["--continue", "--abort"]):
        code, _err = _rebase(stack_repo, argv, monkeypatch)
        assert code == 2, argv
    assert rebase_in_progress(stack_repo)
