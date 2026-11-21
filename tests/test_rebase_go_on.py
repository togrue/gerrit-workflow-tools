# Spec: docu/spec/commands/rebase.md
"""``ger rebase --continue`` / ``--abort`` / ``--skip``: going on with the rebase underway, as git does."""

from __future__ import annotations

from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_edit import main as edit_main
from gerrit_workflow_tools.cli_rebase import main as rebase_main
from gerrit_workflow_tools.core.git_run import git, git_out
from gerrit_workflow_tools.core.rebase_state import RebaseAction, RebaseStop, rebase_in_progress, rebase_progress
from gerrit_workflow_tools.server.api import GerApi
from tests.change_store import ChangeStore
from tests.conftest import run_cli
from tests.test_rebasing_stack import _am_stopped, _move_upstream_over_c_txt, _stop_to_edit, _store
from tests.test_serve import _get


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


CONTINUE, ABORT, SKIP = RebaseAction.CONTINUE, RebaseAction.ABORT, RebaseAction.SKIP


def _resolve_c_txt(repo: Path) -> None:
    (repo / "c.txt").write_text("resolved\n", encoding="utf-8")
    git("add", "c.txt", cwd=repo)


def _actions(repo: Path) -> tuple[RebaseAction, ...]:
    progress = rebase_progress(repo)
    assert progress is not None
    return progress.actions


def test_skip_only_at_a_stop_on_a_conflict_and_continue_only_once_nothing_is_unmerged(stack_repo: Path) -> None:
    _stop_to_edit(stack_repo)
    assert _actions(stack_repo) == (CONTINUE, ABORT)
    git("rebase", "--abort", cwd=stack_repo)
    _stopped_on_c_txt(stack_repo)
    assert _actions(stack_repo) == (ABORT, SKIP)
    _resolve_c_txt(stack_repo)
    assert _actions(stack_repo) == (CONTINUE, ABORT, SKIP)


def test_ger_serve_serves_the_ways_on_core_allows(stack_repo: Path) -> None:
    store = _store(stack_repo)
    _stop_to_edit(stack_repo)
    assert _get(GerApi(stack_repo, gerrit=store), "/stack")["rebase"]["actions"] == ["continue", "abort"]


def test_skip_at_an_edit_stop_is_refused_and_keeps_the_work_not_committed(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``git rebase --skip`` there resets hard: the edits would be gone and the commit would stay."""
    _stop_to_edit(stack_repo)
    head = git_out("rev-parse", "HEAD", cwd=stack_repo)
    (stack_repo / "c.txt").write_text("work in progress\n", encoding="utf-8")

    code, err = _rebase(stack_repo, ["--skip"], monkeypatch)

    assert code == ExitCode.USAGE
    assert "--skip leaves out a commit the rebase stopped on with a conflict, but it stopped with the commit" in err
    assert rebase_in_progress(stack_repo) and git_out("rev-parse", "HEAD", cwd=stack_repo) == head
    assert (stack_repo / "c.txt").read_text(encoding="utf-8") == "work in progress\n"


def test_continue_over_unstaged_changes_says_to_amend_or_stash_them(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Git would say "You must edit all merge conflicts", at an edit stop with nothing conflicted."""
    _stop_to_edit(stack_repo)
    head = git_out("rev-parse", "HEAD", cwd=stack_repo)
    (stack_repo / "c.txt").write_text("not added\n", encoding="utf-8")

    code, err = _rebase(stack_repo, ["--continue"], monkeypatch)

    assert code == ExitCode.USAGE
    assert (
        "error: you have unstaged changes to c.txt; amend them in (`git commit --amend -a`) "
        "or `git stash` them, then continue"
    ) in err
    assert rebase_in_progress(stack_repo) and git_out("rev-parse", "HEAD", cwd=stack_repo) == head


def test_continue_over_unstaged_changes_at_a_resolved_conflict_says_to_add_them(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stopped_on_c_txt(stack_repo)
    _resolve_c_txt(stack_repo)
    (stack_repo / "c.txt").write_text("resolved, then changed again\n", encoding="utf-8")

    code, err = _rebase(stack_repo, ["--continue"], monkeypatch)

    assert code == ExitCode.USAGE
    assert "you have unstaged changes to c.txt; `git add` them or `git stash` them, then continue" in err


def test_continue_at_an_edit_stop_amends_staged_changes_in(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stop_to_edit(stack_repo)
    (stack_repo / "c.txt").write_text("staged\n", encoding="utf-8")
    git("add", "c.txt", cwd=stack_repo)

    code, err = _rebase(stack_repo, ["--continue"], monkeypatch)

    assert code == 0, err
    assert not rebase_in_progress(stack_repo)
    assert git_out("show", "feature:c.txt", cwd=stack_repo) == "staged"


def test_without_a_terminal_continue_keeps_the_callers_git_editor(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The caller's own ``GIT_EDITOR`` writes the message: ger defaults it only when it is not set."""
    _stopped_on_c_txt(stack_repo)
    _resolve_c_txt(stack_repo)
    monkeypatch.setenv("GIT_EDITOR", "sed -i -e '1s/^/edited: /'")

    code, err = _rebase(stack_repo, ["--continue"], monkeypatch)

    assert code == 0, err
    assert "edited: test! temporary experiment" in _subjects(stack_repo)


def _edit_next(repo: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[int, str]:
    code, _out, err = run_cli(repo, edit_main, ["-n"], monkeypatch, catch_sys_exit=True, gerrit=_store(repo))
    return code, err


def test_ger_edit_n_goes_on_as_ger_rebase_continue_does(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The same refusal over unstaged changes, and no editor without a terminal."""
    _store(stack_repo)
    _stop_to_edit(stack_repo)
    (stack_repo / "c.txt").write_text("not added\n", encoding="utf-8")

    code, err = _edit_next(stack_repo, monkeypatch)

    assert code == ExitCode.USAGE
    assert "you have unstaged changes to c.txt; amend them in (`git commit --amend -a`)" in err
    git("checkout", "--", "c.txt", cwd=stack_repo)
    git("rebase", "--abort", cwd=stack_repo)

    _stopped_on_c_txt(stack_repo)
    _resolve_c_txt(stack_repo)
    monkeypatch.delenv("GIT_EDITOR", raising=False)
    git("config", "core.editor", "false", cwd=stack_repo)  # an editor that fails, were one opened
    _edit_next(stack_repo, monkeypatch)
    assert not rebase_in_progress(stack_repo)
    assert "test! temporary experiment" in _subjects(stack_repo)
