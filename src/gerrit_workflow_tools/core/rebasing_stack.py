"""The **rebasing stack**: the local stack while a rebase is underway, and where it stands on each commit.

How far the rebase got is :mod:`gerrit_workflow_tools.core.rebase_state`; this lays that over the
commits, as they are now on ``HEAD`` and as they were in the todo.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path

from gerrit_workflow_tools.core.rebase_state import RebaseProgress, rebase_progress, uncommitted_files
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

    @property
    def folding_into(self) -> Commit | None:
        """The commit a stopped fixup or squash folds into (:attr:`RebaseProgress.folding_into`), as shown."""
        target = self.rebase.folding_into
        return next((c for c in self.commits if target and c.sha == target), None)


def _same_change(a: Commit, b: Commit) -> bool:
    """Whether *a* and *b* are one change: the same Change-Id, else the same subject.

    A resolution committed with ``git commit --no-edit`` keeps git's ``# Conflicts:`` lines below
    the footer, so its Change-Id is no footer any more: only the subject is left to go by.
    """
    if a.valid_change_id and b.valid_change_id:
        return a.valid_change_id.lower() == b.valid_change_id.lower()
    return a.subject == b.subject


def _stopped_on_head(
    replayed: list[Commit], start: int, made_at: int, rebase: RebaseProgress, was: Commit | None
) -> int | None:
    """Where on ``HEAD`` the commit the rebase stopped at is now, or ``None`` while it is not there.

    Applied whole, it is where git left ``HEAD`` (``amend``), or at ``HEAD`` when git noted no
    amend. Otherwise it may be among the commits made by hand from *made_at* on: an amend of it, a
    re-made commit, or a conflict resolution committed instead of continued.
    """
    if rebase.current is None:
        return None
    if rebase.applied and rebase.amend is None:
        return len(replayed) - 1 if len(replayed) > start else None
    if rebase.applied:
        at = next((i for i, row in enumerate(replayed) if row.sha == rebase.amend), None)
        if at is not None:
            return at
    if was is None:
        return None
    return next((i for i in range(made_at, len(replayed)) if _same_change(replayed[i], was)), None)


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
    # The top commits from made_at on are not git's: made by hand at the stop.
    made_at = next((i + 1 for i in range(len(replayed) - 1, start - 1, -1) if replayed[i].sha in rebase.settled), start)
    wanted = [rebase.current, *rebase.todo] if rebase.current else list(rebase.todo)
    read = read_commit_rows(cwd, "--no-walk=unsorted", *wanted) if wanted else []
    found = [next((r for r in read if r.sha.startswith(want)), None) for want in wanted]
    was = found.pop(0) if rebase.current else None

    rows: list[Commit] = []
    if (at := _stopped_on_head(replayed, start, made_at, rebase, was)) is not None:
        steps[at] = RebaseStep.CURRENT
        rebase = replace(rebase, applied=True, moved=at < len(replayed) - 1, uncommitted=uncommitted_files(cwd))
    elif rebase.applied and rebase.amend is not None:
        rebase = replace(rebase, moved=True)  # applied, then reset away: maybe split into new commits
    elif was is not None and not rebase.applied:
        rows.append(was)
        steps.append(RebaseStep.CURRENT)
    todo = [row for row in found if row is not None]
    steps += [RebaseStep.TODO] * len(todo)
    return RebasingStack(rebase=rebase, commits=(*replayed, *rows, *todo), steps=tuple(steps))


def stopped_rebase(cwd: Path | str | None, *, repo_dir: Path | None = None) -> RebaseProgress | None:
    """Where the rebase underway stopped, as ``GET /stack`` serves it; ``None`` without one.

    :func:`rebase_progress` as the rebasing stack refines it (a conflict whose resolution was
    committed by hand is an ``edit``, an edit stop ``HEAD`` moved on from a ``split``), read above
    ``onto``: what ``ger rebase --continue|--skip`` and ``ger edit -n`` go by, so they refuse what
    ``rebase.actions`` leaves out. One ``git log`` and one ``git show`` more, on a rebase that keeps a todo.
    """
    progress = rebase_progress(cwd, repo_dir=repo_dir)
    if progress is None or not progress.keeps_todo or progress.onto is None:
        return progress
    return load_rebasing_stack(cwd, progress.onto, progress).rebase
