"""The **rebasing stack**: the local stack while a rebase is underway, and where it stands on each commit.

How far the rebase got is :mod:`gerrit_workflow_tools.core.rebase_state`; this lays that over the
commits, as they are now on ``HEAD`` and as they were in the todo.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path

from gerrit_workflow_tools.core.rebase_state import RebaseProgress, RebaseStop
from gerrit_workflow_tools.core.stack import Commit, commits_in_range, read_commit_rows


class RebaseStep(Enum):
    """Where a rebase underway stands on one commit of the **local stack**."""

    DONE = "done"
    """Replayed already: the commit as it is now."""
    CURRENT = "current"
    """The commit it stopped at: as it is now when applied, else as it was."""
    TODO = "todo"
    """Still to replay: the commit as it was."""


@dataclass(frozen=True)
class RebasingStack:
    """The **local stack** while a rebase is underway: what it made so far, then what it has still to replay.

    The commits on ``HEAD`` above the stack's base come first, as they are now; then the commit
    it stopped at when that is not applied yet, and the todo, as they were. :attr:`steps` runs
    beside :attr:`commits`: ``None`` for a commit at or below the rebase's ``onto``, which it
    does not replay.
    """

    rebase: RebaseProgress
    commits: tuple[Commit, ...] = ()
    steps: tuple[RebaseStep | None, ...] = ()

    @property
    def current(self) -> Commit | None:
        """The commit the rebase stopped at, as the stack shows it."""
        return next((c for c, step in zip(self.commits, self.steps, strict=True) if step is RebaseStep.CURRENT), None)


def _same_change(a: Commit, b: Commit) -> bool:
    """Whether *a* and *b* are one change: the same Change-Id, else the same subject.

    A resolution committed with ``git commit --no-edit`` keeps git's ``# Conflicts:`` lines below
    the footer, so its Change-Id is no footer any more: only the subject is left to go by.
    """
    if a.valid_change_id and b.valid_change_id:
        return a.valid_change_id.lower() == b.valid_change_id.lower()
    return a.subject == b.subject


def load_rebasing_stack(
    cwd: Path | str | None, base: str, rebase: RebaseProgress, *, first_parent: bool = True
) -> RebasingStack:
    """The :class:`RebasingStack` above *base*: one ``git log``, a second for the stopped commit and the todo.

    *base* is the branch's upstream, or ``onto`` for a detached HEAD's rebase. Where ``HEAD`` moved
    on from where git stopped (a resolution committed by hand, a split at an edit stop), the
    commits made there are ``done`` and :attr:`RebasingStack.rebase` says so
    (:attr:`RebaseProgress.applied`, :attr:`RebaseProgress.moved`).
    """
    replayed = commits_in_range(cwd, f"{base}..HEAD", first_parent=first_parent)
    start = next((i + 1 for i, row in enumerate(replayed) if row.sha == rebase.onto), 0)
    steps: list[RebaseStep | None] = [None] * start + [RebaseStep.DONE] * (len(replayed) - start)
    # The top commits git did not make: made by hand at the stop.
    made = len(replayed) - next(
        (i + 1 for i in range(len(replayed) - 1, start - 1, -1) if replayed[i].sha in rebase.settled), start
    )
    at = next((i for i, row in enumerate(replayed) if rebase.amend and row.sha == rebase.amend), None)
    # The stopped commit as it was: a row while not applied; for an edit, only to tell an amend once HEAD left it.
    stopped = [rebase.current] if rebase.current and (not rebase.applied or (rebase.amend and at is None)) else []
    pending = [*stopped, *rebase.todo]
    read = read_commit_rows(cwd, "--no-walk=unsorted", *pending) if pending else []
    found = [next((r for r in read if r.sha.startswith(want)), None) for want in pending]
    was = found[0] if stopped else None
    if not rebase.applied and was is not None and made:
        # Resolved and committed by hand: the commit is applied, at HEAD or below what was made on it.
        made_at = len(replayed) - made
        mine = next((i for i in range(made_at, len(replayed)) if _same_change(replayed[i], was)), None)
        if mine is not None:
            steps[mine] = RebaseStep.CURRENT
            rebase = replace(rebase, applied=True, unmerged=(), moved=mine < len(replayed) - 1)
    elif rebase.stop is RebaseStop.EDIT:
        if at is not None:
            steps[at] = RebaseStep.CURRENT
            rebase = replace(rebase, moved=at < len(replayed) - 1)
        elif rebase.amend is None and len(replayed) > start:
            steps[-1] = RebaseStep.CURRENT  # applied, with nothing to tell where git left HEAD
        elif made == 1 and was is not None and _same_change(replayed[-1], was):
            steps[-1] = RebaseStep.CURRENT  # amended: on where the stopped commit sat, the same change
        elif rebase.amend is not None:
            rebase = replace(rebase, moved=True)  # reset away, maybe split into new commits
    if stopped and rebase.applied:
        found[0] = None
    rows: list[Commit] = []
    for index, row in enumerate(found):
        if row is not None:
            rows.append(row)
            steps.append(RebaseStep.CURRENT if index < len(stopped) else RebaseStep.TODO)
    return RebasingStack(rebase=rebase, commits=(*replayed, *rows), steps=tuple(steps))
