"""The **rebasing stack**: the local stack while a rebase is underway, and where it stands on each commit.

How far the rebase got is :mod:`gerrit_workflow_tools.core.rebase_state`; this lays that over the
commits, as they are now on ``HEAD`` and as they were in the todo.
"""

from __future__ import annotations

from dataclasses import dataclass
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


def load_rebasing_stack(
    cwd: Path | str | None, base: str, rebase: RebaseProgress, *, first_parent: bool = True
) -> RebasingStack:
    """The :class:`RebasingStack` above *base* (the branch's upstream): one ``git log``, a second for a todo."""
    replayed = commits_in_range(cwd, f"{base}..HEAD", first_parent=first_parent)
    start = next((i + 1 for i, row in enumerate(replayed) if row.sha == rebase.onto), 0)
    steps: list[RebaseStep | None] = [None] * start + [RebaseStep.DONE] * (len(replayed) - start)
    if rebase.stop is RebaseStop.EDIT and len(replayed) > start:
        steps[-1] = RebaseStep.CURRENT
    stopped = [rebase.current] if rebase.current and not rebase.applied else []
    pending = [*stopped, *rebase.todo]
    read = read_commit_rows(cwd, "--no-walk=unsorted", *pending) if pending else []
    rows: list[Commit] = []
    for index, want in enumerate(pending):
        if (row := next((r for r in read if r.sha.startswith(want)), None)) is not None:
            rows.append(row)
            steps.append(RebaseStep.CURRENT if index < len(stopped) else RebaseStep.TODO)
    return RebasingStack(rebase=rebase, commits=(*replayed, *rows), steps=tuple(steps))
