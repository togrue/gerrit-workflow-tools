"""What to do at a rebase underway: the refusals of each way on, and the next step at a stop.

Every ger command that starts or goes on with a rebase ends a stop with these words, and every
command that must not run on a conflicted index refuses with them, so each reason has one
wording. Where the rebase stands is :mod:`gerrit_workflow_tools.core.rebase_state`.
"""

from __future__ import annotations

from collections.abc import Collection
from pathlib import Path

from gerrit_workflow_tools.core.attention import Attention
from gerrit_workflow_tools.core.git_run import git
from gerrit_workflow_tools.core.git_state import am_underway, git_dir, rebase_underway
from gerrit_workflow_tools.core.rebase_state import (
    AM_UNDERWAY,
    RebaseAction,
    RebaseProgress,
    RebaseStop,
    am_in_progress,
    nul_paths,
    rebase_in_progress,
    unmerged_paths,
)


CONTINUE = "`ger rebase --continue`"
SKIP = "`ger rebase --skip`"
ABORT = "`ger rebase --abort`"
FINISH = f"finish it ({CONTINUE}) or {ABORT}"
"""The way out of a rebase underway, for a command that cannot run during one."""
_BACK = f"{ABORT} goes back to where the rebase started"


def rebase_refusal(cwd: Path | str | None) -> str | None:
    """Why a command that moves branches cannot start during a rebase or a ``git am``, or ``None``."""
    repo_dir = git_dir(cwd)
    if rebase_underway(repo_dir):
        return f"a rebase is already in progress; {FINISH} first"
    if am_underway(repo_dir):
        return f"{AM_UNDERWAY} first"
    return None


def _walks_on(walk_on: str | None) -> str:
    """How *walk_on* differs from ``ger rebase --continue``: it also goes on to the next attention stop."""
    return f"; `{walk_on}` finishes and goes on to the next commit needing attention" if walk_on else ""


def conflict_next_step(cwd: Path | str | None, *, walk_on: str | None = None) -> str | None:
    """The one next step after a rebase stopped on a conflict, or ``None`` when none is stopped on one.

    Every ger command that starts or continues a rebase prints this when it stops on a conflict,
    and every command that must not run on a conflicted index refuses with it. ``ger rebase
    --continue`` finishes; *walk_on* names the command that also walks on, where a walk is underway.
    """
    paths = unmerged_paths(cwd)
    if not paths:
        return None
    if not rebase_in_progress(cwd):
        return f"resolve the conflicts in {', '.join(paths)} and `git add` them, then finish the merge or pick"
    return _resolve_step(paths, walk_on)


def _resolve_step(paths: tuple[str, ...] | list[str], walk_on: str | None) -> str:
    """:func:`conflict_next_step` of a rebase stopped with *paths* unmerged."""
    return (
        f"resolve the conflicts in {', '.join(paths)}, `git add` them, then {CONTINUE} to finish"
        f"{_walks_on(walk_on)}; {SKIP} leaves this commit out, {_BACK}"
    )


def stop_next_step(progress: RebaseProgress, *, walk_on: str | None = None) -> str:
    """The one next step at the stop *progress* is at, whatever stopped it: what every ger-started rebase ends with.

    *walk_on* names the command that also walks on to the next attention stop, where a walk is underway.
    """
    if progress.stop is RebaseStop.CONFLICT:
        return _resolve_step(progress.unmerged, walk_on)
    if progress.stop is RebaseStop.EDIT:
        return edit_stop_next_step(walk_on)
    step = (
        {
            RebaseStop.RESOLVED: f"its conflicts are resolved: {CONTINUE} commits it and goes on",
            RebaseStop.SPLIT: f"commit what is left of it, then {CONTINUE}",
            RebaseStop.PAUSED: f"{CONTINUE} once you are done here",
        }.get(progress.stop)
        if progress.stop
        else None
    )
    return f"{step or f'{CONTINUE} goes on'}{_walks_on(walk_on)}; {_BACK}"


def stop_refusal(progress: RebaseProgress, action: RebaseAction, *, walk_on: str | None = None) -> str | None:
    """Why *action* is not a way on from the stop *progress* is at (:attr:`RebaseProgress.actions`), or ``None``.

    Told from the stop alone; ``ger rebase`` also refuses to continue over unstaged changes
    (:func:`go_on_refusal`). *walk_on* names the command that also walks on, where a walk is underway.
    """
    if action in progress.actions:
        return None
    if action is RebaseAction.CONTINUE:
        if progress.unmerged:
            return f"the index has unresolved conflicts; {_resolve_step(progress.unmerged, walk_on)}"
        return (
            "the commit it stopped on was committed by hand, so nothing is left to apply and "
            f"continuing would stop again: {SKIP} goes on from HEAD, or {ABORT}"
        )
    if progress.committed_by_hand:
        return (
            "--skip leaves out the commit the rebase stopped on, but it was committed by hand "
            "(HEAD has moved on from where it stopped): skipping now would keep that commit and "
            "throw away your uncommitted changes; commit them or `git stash` them first, "
            f"or {ABORT}"
        )
    why = _SKIP_REFUSALS.get(progress.stop) if progress.stop else None
    return (
        f"--skip leaves out a commit the rebase stopped on with a conflict, but "
        f"{why or 'ger cannot tell where it stopped'}: "
        "skipping would only throw away your uncommitted changes; "
        f"go on with {CONTINUE}, or {ABORT}"
    )


def _unstaged_paths(cwd: Path | str | None) -> list[str]:
    """Tracked paths whose work tree differs from the index, a submodule's own state left out: git's own check.

    ``git rebase --continue`` refreshes the index first, then asks ``diff-files``, so a file only
    touched, or changed only in line endings ``core.autocrlf`` takes back, is no change.
    """
    git("update-index", "-q", "--refresh", cwd=cwd, check=False)
    return nul_paths(git("diff-files", "--name-only", "-z", "--ignore-submodules", cwd=cwd, check=False).stdout)


_SKIP_REFUSALS = {
    RebaseStop.EDIT: "it stopped with the commit applied (an `edit`, or a conflict's resolution committed by hand)",
    RebaseStop.SPLIT: "it stopped with the commit applied (a split)",
    RebaseStop.PAUSED: "it stopped between commits (`exec`, `break`), with no commit to leave out",
}


def go_on_refusal(
    cwd: Path | str | None, action: RebaseAction, progress: RebaseProgress | None, *, walk_on: str | None = None
) -> str | None:
    """Why ``git rebase --<action>`` must not run now, or ``None``: the one check before every ger "go on".

    *progress* is where the rebase stopped as ``GET /stack`` serves it
    (:func:`~gerrit_workflow_tools.core.rebasing_stack.stopped_rebase`), so the CLI and the
    server go by one verdict (:func:`stop_refusal`). Beyond it: git refuses to continue
    over unstaged changes to tracked files with "You must edit all merge conflicts", whatever the
    stop; this says what to do instead. *walk_on* names the command that also walks on, where a
    walk is underway.
    """
    if progress is None:
        if am_in_progress(cwd):
            return f"no rebase is in progress: {AM_UNDERWAY}"
        return f"no rebase is in progress, nothing to {action.value}: {where_head_is(cwd)}"
    if refusal := stop_refusal(progress, action, walk_on=walk_on):
        return refusal
    if action is RebaseAction.CONTINUE and (paths := _unstaged_paths(cwd)):
        on_conflict = progress.stop in (RebaseStop.CONFLICT, RebaseStop.RESOLVED)
        take = "`git add` them" if on_conflict else "amend them in (`git commit --amend -a`)"
        return f"you have unstaged changes to {', '.join(paths)}; {take} or `git stash` them, then {CONTINUE}"
    return None


def edit_stop_next_step(walk_on: str | None) -> str:
    """The next step at a commit an edit stopped on: amend, then finish, or finish and walk on with *walk_on*."""
    return f"amend it (`git commit --amend`), then {CONTINUE} to finish{_walks_on(walk_on)}"


def attention_stop_hint(reasons: Collection[str]) -> str | None:
    """How to see why a walk stopped at a commit with edit attention *reasons*, and how to answer; ``None``: none.

    An attention walk stops where comments are unresolved, the build failed or a reviewer voted
    against the change (``EDIT_ATTENTION_REASONS``).
    """
    hints = []
    if Attention.UNRESOLVED_COMMENTS in reasons:
        hints.append("once fixed, answer each comment `ger show` lists: `ger reply PATH:LINE --done` (or `-m TEXT`)")
    if Attention.CI_FAILED in reasons:
        hints.append("`ger ci` shows what failed in the build")
    if Attention.REVIEW_ISSUES in reasons:
        hints.append("the reviewer says why in their comments or their message on Gerrit")
    return "; ".join(hints) or None


def where_head_is(cwd: Path | str | None) -> str:
    """Where the branch is, in words a refusal or an abort ends with: ``s12 is at 8b6681f trig: add sin/cos``."""
    branch = git("symbolic-ref", "-q", "--short", "HEAD", cwd=cwd, check=False).stdout.strip()
    head = git("log", "-1", "--format=%h %s", cwd=cwd, check=False).stdout.strip()
    return f"{branch or 'HEAD (detached)'} is at {head or 'no commit'}"
