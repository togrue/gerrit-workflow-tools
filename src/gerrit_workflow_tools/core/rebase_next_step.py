"""What to do at a rebase underway: the refusals of each way on, and the next step at a stop.

Every ger command that starts or goes on with a rebase ends a stop with these words, and every
command that must not run on a conflicted index refuses with them, so each reason has one
wording. Where the rebase stands is :mod:`gerrit_workflow_tools.core.rebase_state`.
"""

from __future__ import annotations

from pathlib import Path

from gerrit_workflow_tools.core.git_run import git
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


def _walks_on(walk_on: str | None) -> str:
    """How *walk_on* differs from ``git rebase --continue``: it also goes on to the next attention stop."""
    return f"; `{walk_on}` finishes and goes on to the next commit needing attention" if walk_on else ""


def conflict_next_step(cwd: Path | str | None, *, walk_on: str | None = None) -> str | None:
    """The one next step after a rebase stopped on a conflict, or ``None`` when none is stopped on one.

    Every ger command that starts or continues a rebase prints this when it stops on a conflict,
    and every command that must not run on a conflicted index refuses with it. ``git rebase
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
        f"resolve the conflicts in {', '.join(paths)}, `git add` them, then `git rebase --continue` to finish"
        f"{_walks_on(walk_on)}; `git rebase --abort` goes back to where the rebase started"
    )


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
            "`git rebase --continue` would stop again: `git rebase --skip` goes on from HEAD, "
            "or `git rebase --abort`"
        )
    if progress.committed_by_hand:
        return (
            "--skip leaves out the commit the rebase stopped on, but it was committed by hand "
            "(HEAD has moved on from where it stopped): `git rebase --skip` would keep that commit and "
            "throw away your uncommitted changes; commit them or `git stash` them first, "
            "or `git rebase --abort`"
        )
    why = _SKIP_REFUSALS.get(progress.stop) if progress.stop else None
    return (
        f"--skip leaves out a commit the rebase stopped on with a conflict, but "
        f"{why or 'ger cannot tell where it stopped'}: "
        "`git rebase --skip` would only throw away your uncommitted changes; "
        "go on with `git rebase --continue`, or `git rebase --abort`"
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
        return f"no rebase is in progress: {AM_UNDERWAY}" if am_in_progress(cwd) else "no rebase is in progress"
    if refusal := stop_refusal(progress, action, walk_on=walk_on):
        return refusal
    if action is RebaseAction.CONTINUE and (paths := _unstaged_paths(cwd)):
        on_conflict = progress.stop in (RebaseStop.CONFLICT, RebaseStop.RESOLVED)
        take = "`git add` them" if on_conflict else "amend them in (`git commit --amend -a`)"
        return f"you have unstaged changes to {', '.join(paths)}; {take} or `git stash` them, then continue"
    return None


def edit_stop_next_step(walk_on: str) -> str:
    """The next step at a commit an edit stopped on: amend, then finish, or finish and walk on with *walk_on*."""
    return f"amend it, then `git rebase --continue` to finish{_walks_on(walk_on)}"
