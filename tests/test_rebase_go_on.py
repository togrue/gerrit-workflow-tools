# Spec: docu/spec/commands/rebase.md
"""``ger rebase --continue`` / ``--abort`` / ``--skip``: going on with the rebase underway, as git does."""

from __future__ import annotations

import io
import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_edit import main as edit_main
from gerrit_workflow_tools.cli_log import main as log_main
from gerrit_workflow_tools.cli_rebase import main as rebase_main
from gerrit_workflow_tools.core.git_run import git, git_out
from gerrit_workflow_tools.core.rebase_next_step import go_on_refusal
from gerrit_workflow_tools.core.rebase_state import RebaseAction, RebaseStop, rebase_in_progress, rebase_progress
from gerrit_workflow_tools.core.rebasing_stack import stopped_rebase
from gerrit_workflow_tools.server.api import GerApi
from tests.change_store import ChangeStore
from tests.conftest import run_cli
from tests.helpers import add_submodule
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
    # Said, not left to guess: the branch is back where it was.
    head = git_out("log", "-1", "--format=%h %s", cwd=stack_repo)
    assert err.strip().endswith(f"rebase aborted: feature is at {head}, as before the rebase")


def test_a_stop_ger_rebase_ran_into_names_the_ger_way_on(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An ``edit`` line in the todo: the stop says where it is and to go on with ``ger rebase --continue``."""
    monkeypatch.setenv("GREBASE_EDITOR", "sed -i -e 1s/^pick/edit/")
    first = git_out("log", "-1", "--format=%h %s", "HEAD~2", cwd=stack_repo)

    code, err = _rebase(stack_repo, ["HEAD~3"], monkeypatch)

    assert code == 0, err
    assert rebase_in_progress(stack_repo)
    # git's own instructions ("You can amend the commit now, …", "Stopped at …") are not shown beside ger's.
    assert err.splitlines() == [
        f"stopped at {first}",
        "next: amend it (`git commit --amend`), then `ger rebase --continue` to finish",
    ]


def test_a_conflict_says_ger_s_way_on_and_none_of_git_s_advice(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """git's hints (`git rebase --continue`, …), its "could not apply" and its progress go: ger says it once."""
    _move_upstream_over_c_txt(stack_repo)
    monkeypatch.setenv("GREBASE_EDITOR", "true")
    stopped = git_out("log", "-1", "--format=%h %s", "HEAD~1", cwd=stack_repo)

    code, err = _rebase(stack_repo, ["--onto-remote"], monkeypatch)

    assert code != 0 and rebase_in_progress(stack_repo), err
    # Only ger's lines: git's merge says what conflicts on stdout, which stays as it is.
    assert err.splitlines() == [
        f"stopped at {stopped} (conflict)",
        "next: resolve the conflicts in c.txt, `git add` them, then `ger rebase --continue` to finish; "
        "`ger rebase --skip` leaves this commit out, `ger rebase --abort` goes back to where the rebase started",
    ]

    # Going on from a stop says the same, with git's progress gone.
    (stack_repo / "c.txt").write_text("resolved\n", encoding="utf-8")
    git("add", "c.txt", cwd=stack_repo)
    code, err = _rebase(stack_repo, ["--continue"], monkeypatch)
    assert code == 0, err
    assert "Rebasing (" not in err and "Successfully rebased" not in err
    assert err.splitlines()[0].startswith("rebase finished: ")


def test_without_a_rebase_there_is_nothing_to_go_on_with(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for how in ("--continue", "--abort", "--skip"):
        code, err = _rebase(stack_repo, [how], monkeypatch)
        head = git_out("log", "-1", "--format=%h %s", cwd=stack_repo)
        # Nothing to go on with, and where the branch is: what the user is left with.
        want = f"error: no rebase is in progress, nothing to {how[2:]}: feature is at {head}"
        assert (code, err.strip()) == (ExitCode.USAGE, want)


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
        "or `git stash` them, then `ger rebase --continue`"
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
    assert "you have unstaged changes to c.txt; `git add` them or `git stash` them, then `ger rebase --continue`" in err


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


def test_continue_over_a_line_ending_change_git_sees_is_refused_as_git_would(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``core.autocrlf`` and CRLF over an LF blob: ``git diff`` sees nothing, git's refreshed check
    does. On Windows the fixture writes its files in text mode, so the blob may already hold CR LF
    and there is no change at all; ger is held to what ``git rebase --continue`` itself does on a
    copy of the same repository."""
    _stop_to_edit(stack_repo)
    git("config", "core.autocrlf", "true", cwd=stack_repo)
    text = (stack_repo / "c.txt").read_text(encoding="utf-8")
    (stack_repo / "c.txt").write_bytes(text.replace("\n", "\r\n").encode())
    copy = stack_repo.parent / "copy"
    shutil.copytree(stack_repo, copy, symlinks=True)
    by_git = git("rebase", "--continue", cwd=copy, check=False, env={**os.environ, "GIT_EDITOR": "true"})
    git_refuses = by_git.returncode != 0
    if git_refuses:
        assert "You must edit all merge conflicts" in by_git.stdout, by_git  # git's refusal over unstaged changes

    code, err = _rebase(stack_repo, ["--continue"], monkeypatch)

    if git_refuses:
        assert code == ExitCode.USAGE
        assert "you have unstaged changes to c.txt" in err
        assert rebase_in_progress(stack_repo)
    else:
        assert code == 0, err
        assert not rebase_in_progress(stack_repo)


def test_continue_counts_what_git_counts_as_unstaged(
    stack_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file added with ``-N`` is unstaged; a mode change under ``core.fileMode=false``, a file only
    touched, and a submodule at another commit are not."""
    add_submodule(stack_repo, tmp_path)
    _stop_to_edit(stack_repo, onto="HEAD~1")
    (stack_repo / "new.txt").write_text("new\n", encoding="utf-8")
    git("add", "-N", "new.txt", cwd=stack_repo)
    code, err = _rebase(stack_repo, ["--continue"], monkeypatch)
    assert code == ExitCode.USAGE
    assert "you have unstaged changes to new.txt;" in err

    git("rm", "-q", "--cached", "new.txt", cwd=stack_repo)
    git("config", "core.fileMode", "false", cwd=stack_repo)
    (stack_repo / "c.txt").chmod(0o755)
    (stack_repo / "c.txt").write_text((stack_repo / "c.txt").read_text(encoding="utf-8"), encoding="utf-8")
    git("checkout", "-q", "HEAD~", cwd=stack_repo / "lib")
    code, err = _rebase(stack_repo, ["--continue"], monkeypatch)
    assert code == 0, err
    assert not rebase_in_progress(stack_repo)


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


def _committed_by_hand(repo: Path, subject: str | None = None) -> str:
    """Stopped on the c.txt conflict, resolved and committed with ``git commit``, not ``--continue``."""
    _stopped_on_c_txt(repo)
    _resolve_c_txt(repo)
    git("commit", "-q", *(("-m", subject) if subject else ("--no-edit",)), cwd=repo)
    return git_out("rev-parse", "HEAD", cwd=repo)


def _served(repo: Path) -> dict[str, object]:
    rebase = _get(GerApi(repo, gerrit=ChangeStore({})), "/stack")["rebase"]
    assert isinstance(rebase, dict)
    return rebase


def test_skip_after_a_resolution_committed_by_hand_is_refused_and_keeps_the_work(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``git rebase --skip`` there resets hard and goes on: the commit made by hand stays, the work is gone."""
    head = _committed_by_hand(stack_repo)
    with (stack_repo / "c.txt").open("a", encoding="utf-8") as f:
        f.write("extra\n")

    code, err = _rebase(stack_repo, ["--skip"], monkeypatch)

    assert code == ExitCode.USAGE
    assert "it stopped with the commit applied (an `edit`, or a conflict's resolution committed by hand)" in err
    assert rebase_in_progress(stack_repo) and git_out("rev-parse", "HEAD", cwd=stack_repo) == head
    assert (stack_repo / "c.txt").read_text(encoding="utf-8") == "resolved\nextra\n"
    # The unstaged change is served with the stop: Continue waits for it, before anybody presses it.
    served = _served(stack_repo)
    assert (served["stop"], served["actions"], served["uncommitted"]) == ("edit", ["abort"], ["c.txt"])


def test_continue_over_unstaged_changes_after_a_resolution_committed_by_hand_says_to_amend_them(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The conflict is committed: ``git add`` would not take the changes into anything, an amend does."""
    _committed_by_hand(stack_repo)
    (stack_repo / "c.txt").write_text("changed after the commit\n", encoding="utf-8")

    code, err = _rebase(stack_repo, ["--continue"], monkeypatch)

    assert code == ExitCode.USAGE
    assert "you have unstaged changes to c.txt; amend them in (`git commit --amend -a`) or `git stash` them" in err


def test_a_resolution_committed_by_hand_as_another_commit_is_skipped_only_once_nothing_is_left_to_lose(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another subject and no Change-Id: no telling it is the stopped commit, but HEAD is not where git left it."""
    head = _committed_by_hand(stack_repo, "My own resolution")
    (stack_repo / "c.txt").write_text("staged work\n", encoding="utf-8")
    git("add", "c.txt", cwd=stack_repo)
    served = _served(stack_repo)
    assert (served["stop"], served["applied"], served["actions"]) == (None, False, ["continue", "abort"])

    code, err = _rebase(stack_repo, ["--skip"], monkeypatch)

    assert code == ExitCode.USAGE
    assert "but it was committed by hand (HEAD has moved on from where it stopped)" in err
    assert "commit them or `git stash` them first" in err
    assert git_out("rev-parse", "HEAD", cwd=stack_repo) == head
    assert git_out("show", ":c.txt", cwd=stack_repo) == "staged work"

    git("stash", "-q", cwd=stack_repo)
    assert _served(stack_repo)["actions"] == ["continue", "abort", "skip"]
    code, err = _rebase(stack_repo, ["--skip"], monkeypatch)
    assert code == 0, err
    assert not rebase_in_progress(stack_repo)
    assert "My own resolution" in _subjects(stack_repo)


def test_skip_at_a_split_stop_says_it_is_a_split(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stop_to_edit(stack_repo)
    git("reset", "-q", "HEAD~", cwd=stack_repo)
    git("add", "-A", cwd=stack_repo)
    git("commit", "-q", "-m", "c part 1", cwd=stack_repo)

    code, err = _rebase(stack_repo, ["--skip"], monkeypatch)

    assert code == ExitCode.USAGE
    assert "but it stopped with the commit applied (a split)" in err


def _apply_committed_by_hand(repo: Path) -> str:
    _move_upstream_over_c_txt(repo)
    git("rebase", "-q", "--apply", "origin/main", cwd=repo, check=False)
    _resolve_c_txt(repo)
    git("commit", "-q", "-m", "resolved by hand", cwd=repo)
    return git_out("rev-parse", "HEAD", cwd=repo)


def test_under_the_apply_backend_a_resolution_committed_by_hand_goes_on_by_skip(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``git am --continue`` would stop again ("No changes"); ``--skip`` keeps HEAD and goes on."""
    head = _apply_committed_by_hand(stack_repo)
    served = _served(stack_repo)
    assert (served["stop"], served["actions"]) == (None, ["abort", "skip"])

    code, err = _rebase(stack_repo, ["--continue"], monkeypatch)
    assert code == ExitCode.USAGE
    assert "committed by hand, so nothing is left to apply" in err and "`ger rebase --skip` goes on from HEAD" in err
    assert git_out("rev-parse", "HEAD", cwd=stack_repo) == head

    code, err = _rebase(stack_repo, ["--skip"], monkeypatch)
    assert code == 0, err
    assert not rebase_in_progress(stack_repo)
    assert "resolved by hand" in _subjects(stack_repo)


def test_under_the_apply_backend_staged_work_on_a_resolution_committed_by_hand_goes_on_by_continue(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _apply_committed_by_hand(stack_repo)
    (stack_repo / "c.txt").write_text("more\n", encoding="utf-8")
    git("add", "c.txt", cwd=stack_repo)

    assert _served(stack_repo)["actions"] == ["continue", "abort"]
    code, err = _rebase(stack_repo, ["--skip"], monkeypatch)
    assert code == ExitCode.USAGE
    assert "commit them or `git stash` them first" in err and "--continue" not in err


def _cli_refusals(repo: Path) -> dict[str, str]:
    """``ger rebase``'s own verdict, without running git: why each way on would be refused now."""
    progress = stopped_rebase(repo)
    return {a.value: why for a in RebaseAction if (why := go_on_refusal(repo, a, progress)) is not None}


def _hand_split(repo: Path) -> None:
    _stop_to_edit(repo)
    git("reset", "-q", "HEAD~", cwd=repo)
    _resolve_c_txt(repo)
    git("commit", "-q", "-m", "c part 1", cwd=repo)


def _resolved(repo: Path) -> None:
    _stopped_on_c_txt(repo)
    _resolve_c_txt(repo)


def _by_hand_other(repo: Path) -> None:
    _committed_by_hand(repo, "My own resolution")


def _by_hand_other_staged(repo: Path) -> None:
    _committed_by_hand(repo, "My own resolution")
    git("rm", "-q", "--cached", "c.txt", cwd=repo)


def _apply_resolved(repo: Path) -> None:
    _apply_committed_by_hand(repo)
    (repo / "c.txt").write_text("more\n", encoding="utf-8")
    git("add", "c.txt", cwd=repo)


@pytest.mark.parametrize(
    "stop_at",
    [
        _stop_to_edit,
        _stopped_on_c_txt,
        _resolved,
        _committed_by_hand,
        _by_hand_other,
        _by_hand_other_staged,
        _hand_split,
        _apply_committed_by_hand,
        _apply_resolved,
    ],
)
def test_ger_rebase_refuses_exactly_what_ger_serve_leaves_out_and_says_why_alike(
    stack_repo: Path, stop_at: Callable[[Path], object]
) -> None:
    """One verdict: ``ger rebase --<action>`` is refused where ``GET /stack`` leaves it out, in the words it serves."""
    stop_at(stack_repo)
    served = _served(stack_repo)
    refused = _cli_refusals(stack_repo)

    actions = served["actions"]
    assert isinstance(actions, list)
    assert sorted(refused) == sorted(a.value for a in RebaseAction if a.value not in actions)
    assert refused == served["refused"]


class _Terminal(io.StringIO):
    """stderr as a terminal: ``say`` takes its width from ``COLUMNS``."""

    def isatty(self) -> bool:
        return True


def test_on_a_terminal_the_next_step_wraps_between_words(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """At 60 columns no line is longer, none breaks inside a word, and piped it is one line (above)."""
    _move_upstream_over_c_txt(stack_repo)
    monkeypatch.setenv("GREBASE_EDITOR", "true")
    monkeypatch.setenv("COLUMNS", "60")
    monkeypatch.chdir(stack_repo)
    terminal = _Terminal()
    monkeypatch.setattr(sys, "stderr", terminal)
    rebase_main(["--onto-remote"], gerrit=ChangeStore({}))
    lines = terminal.getvalue().splitlines()
    assert lines and all(len(line) <= 60 for line in lines), lines
    said = " ".join(line.strip() for line in lines)
    assert "`ger rebase --continue` to finish" in said and "`ger rebase --abort` goes back" in said
    assert [line[:6] for line in lines[2:]] == ["      "] * len(lines[2:]), "the rest indented under next:'s text"


# --- A resolution git rerere reused --------------------------------------------------


def _conflict_at_continue(repo: Path) -> None:
    """Edit the commit below the top so that the top no longer applies: the rebase stops on a conflict there."""
    env = {"GIT_SEQUENCE_EDITOR": "sed -i -e 1s/^pick/edit/"}
    git("rebase", "-q", "-i", "HEAD~2", cwd=repo, env=env)
    (repo / "notes.txt").write_text("amended below\n", encoding="utf-8")
    git("commit", "-q", "-a", "--amend", "--no-edit", cwd=repo)
    assert git("rebase", "--continue", cwd=repo, env={"GIT_EDITOR": "true"}, check=False).returncode != 0


@pytest.mark.parametrize("auto_update", [True, False], ids=["staged", "unstaged"])
def test_a_resolution_rerere_reused_is_said_to_be_its_own(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch, auto_update: bool
) -> None:
    """git replayed a resolution recorded earlier: ger says so and names the file, instead of "resolved"."""
    git("config", "rerere.enabled", "true", cwd=stack_repo)
    git("config", "rerere.autoUpdate", str(auto_update).lower(), cwd=stack_repo)
    (stack_repo / "notes.txt").write_text("first\n", encoding="utf-8")
    git("add", "notes.txt", cwd=stack_repo)
    git("commit", "-q", "-m", "notes: add", cwd=stack_repo)
    (stack_repo / "notes.txt").write_text("second\n", encoding="utf-8")
    git("commit", "-q", "-am", "notes: change", cwd=stack_repo)
    before = git_out("rev-parse", "HEAD", cwd=stack_repo)

    # The first time, resolved by hand: nothing is rerere's, and the resolution is recorded at the commit.
    _conflict_at_continue(stack_repo)
    (stack_repo / "notes.txt").write_text("resolved by hand\n", encoding="utf-8")
    git("add", "notes.txt", cwd=stack_repo)
    progress = rebase_progress(stack_repo)
    assert progress is not None and progress.stop is RebaseStop.RESOLVED and progress.reused == ()
    git("rebase", "--continue", cwd=stack_repo, env={"GIT_EDITOR": "true"})

    # Again: git puts the recorded resolution back by itself.
    git("reset", "-q", "--hard", before, cwd=stack_repo)
    _conflict_at_continue(stack_repo)
    assert (stack_repo / "notes.txt").read_text(encoding="utf-8") == "resolved by hand\n"
    progress = rebase_progress(stack_repo)
    assert progress is not None and progress.reused == ("notes.txt",)
    assert progress.stop is (RebaseStop.RESOLVED if auto_update else RebaseStop.CONFLICT)

    _code, _out, err = run_cli(stack_repo, log_main, ["--color=never"], monkeypatch, gerrit=_store(stack_repo))
    assert "git reused a recorded resolution for notes.txt (rerere): check it" in err, err
    served = _get(GerApi(stack_repo, gerrit=ChangeStore({})), "/stack")
    assert served["rebase"]["reused"] == ["notes.txt"]
    # The way on says to check it first; it does not go on by itself.
    git("rebase", "--abort", cwd=stack_repo)
    git("reset", "-q", "--hard", before, cwd=stack_repo)
    monkeypatch.setenv("GIT_EDITOR", "true")
    env = {"GIT_SEQUENCE_EDITOR": "sed -i -e 1s/^pick/edit/"}
    git("rebase", "-q", "-i", "HEAD~2", cwd=stack_repo, env=env)
    (stack_repo / "notes.txt").write_text("amended below\n", encoding="utf-8")
    git("commit", "-q", "-a", "--amend", "--no-edit", cwd=stack_repo)
    code, _out, err = run_cli(stack_repo, rebase_main, ["--continue"], monkeypatch, gerrit=ChangeStore({}))
    assert code != 0 and rebase_in_progress(stack_repo), "stopped there, for you to look"
    then = "`git add` it, then `ger rebase --continue` to finish" if not auto_update else "then `ger rebase --continue`"
    assert f"next: git reused a recorded resolution for notes.txt (rerere): check it, {then}" in err, err
