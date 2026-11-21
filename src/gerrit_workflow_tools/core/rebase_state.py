"""A rebase underway: how far it got, the local stack while it runs, the refusals and the next step.

Every history-moving ger command asks here before it starts, and every ger-started rebase that
stops on a conflict ends with :func:`conflict_next_step`, so each reason has one wording.
Which branch a stopped rebase belongs to stays with branch resolution in
:mod:`gerrit_workflow_tools.core.git_state`.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from gerrit_workflow_tools.core.git_run import git
from gerrit_workflow_tools.core.git_state import git_dir
from gerrit_workflow_tools.core.stack import Commit, commits_in_range, read_commit_rows
from gerrit_workflow_tools.core.tracked_changes import TrackedChanges, tracked_changes


def rebase_in_progress(cwd: Path | str | None) -> bool:
    """True while git has a rebase underway, including one stopped at a commit."""
    return rebase_underway(git_dir(cwd))


def rebase_underway(repo_dir: Path | None) -> bool:
    """:func:`rebase_in_progress` for a git dir already known (``run.worktree.repository_dir``)."""
    return repo_dir is not None and any((repo_dir / d).exists() for d in ("rebase-merge", "rebase-apply"))


class RebaseStop(Enum):
    """Why a rebase underway is stopped: what the next step is, in core's words, not a shell's."""

    EDIT = "edit"
    """Stopped with the commit it is at applied whole (an ``edit``): amend it, then continue."""
    CONFLICT = "conflict"
    """Stopped on a conflict: :attr:`RebaseProgress.unmerged` names the paths still unresolved."""
    RESOLVED = "resolved"
    """Stopped on a conflict that is resolved and added: ready to continue."""
    PAUSED = "paused"
    """Stopped between commits (``exec``, ``break``), or a rebase that keeps no todo (``rebase-apply``)."""


@dataclass(frozen=True)
class RebaseProgress:
    """Where a rebase underway stands, in the ids of the commits it replays: the branch as it was.

    Ids are as git wrote them into its todo (full once the rebase runs), oldest first.
    """

    done: tuple[str, ...] = ()
    """Commits already replayed, the one it stopped at excluded."""
    current: str | None = None
    """The commit the rebase stopped at; ``None`` when it stopped between commits (``exec``, ``break``)."""
    applied: bool = True
    """``False`` while :attr:`current` is only partly there: it stopped on a conflict."""
    todo: tuple[str, ...] = ()
    """Commits still to replay."""
    branch: str | None = None
    """The branch being rebased (``head-name``); ``None`` for a rebase of a detached HEAD."""
    onto: str | None = None
    """The commit the rebase replays onto."""
    orig_head: str | None = None
    """Where the branch was when the rebase started: it still points there until the rebase ends."""
    unmerged: tuple[str, ...] = ()
    """Paths the index still holds as unresolved conflicts, while :attr:`current` is not applied."""
    keeps_todo: bool = True
    """``False`` for a rebase git runs through ``am`` (``rebase-apply``): it says nothing of its commits."""

    @property
    def stop(self) -> RebaseStop:
        """Why the rebase is stopped (:class:`RebaseStop`)."""
        if self.current is None:
            return RebaseStop.PAUSED
        if self.applied:
            return RebaseStop.EDIT
        return RebaseStop.CONFLICT if self.unmerged else RebaseStop.RESOLVED


_REPLAYING = frozenset({"pick", "p", "reword", "r", "edit", "e", "squash", "s", "fixup", "f"})


def _todo_ids(path: Path) -> list[str | None]:
    """One entry per command line of a rebase todo file: the commit it replays, or ``None``."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    ids: list[str | None] = []
    for line in lines:
        words = line.split()
        if not words or words[0].startswith("#"):
            continue
        args = [word for word in words[1:] if not word.startswith("-")]
        ids.append(args[0].lower() if words[0] in _REPLAYING and args else None)
    return ids


def _state_word(path: Path) -> str | None:
    """The one line git wrote into a rebase state file, or ``None``."""
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def rebase_progress(cwd: Path | str | None, *, repo_dir: Path | None = None) -> RebaseProgress | None:
    """How far the rebase underway got, or ``None`` when there is none.

    Read from git's own state files; one ``git diff`` for the unmerged paths, only while the
    commit it stopped at is not applied. A rebase git runs through ``am`` (``rebase-apply``)
    keeps no todo: it answers its branch and an empty progress. *repo_dir* is the git dir when
    the caller already knows it (``run.worktree.repository_dir``).
    """
    repo_dir = repo_dir if repo_dir is not None else git_dir(cwd)
    if repo_dir is None or not rebase_underway(repo_dir):
        return None
    state = repo_dir / "rebase-merge"
    keeps_todo = state.exists()
    if not keeps_todo:
        state = repo_dir / "rebase-apply"
    done = _todo_ids(state / "done")
    current = done[-1] if done else None
    # A stop with the commit in hand leaves REBASE_HEAD; `amend` is there only when it was applied whole.
    applied = current is None or not (repo_dir / "REBASE_HEAD").exists() or (state / "amend").exists()
    head_name = _state_word(state / "head-name") or ""
    return RebaseProgress(
        done=tuple(sha for sha in done[:-1] if sha),
        current=current,
        applied=applied,
        todo=tuple(sha for sha in _todo_ids(state / "git-rebase-todo") if sha),
        branch=head_name.removeprefix("refs/heads/") if head_name.startswith("refs/heads/") else None,
        onto=_state_word(state / "onto"),
        orig_head=_state_word(state / "orig-head"),
        unmerged=() if applied else tuple(unmerged_paths(cwd)),
        keeps_todo=keeps_todo,
    )


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


def rebase_refusal(cwd: Path | str | None) -> str | None:
    """Why a command that moves branches cannot start during a rebase, or ``None``."""
    if rebase_in_progress(cwd):
        return "a rebase is already in progress; run `git rebase --continue` or `git rebase --abort` first"
    return None


def unmerged_paths(cwd: Path | str | None) -> list[str]:
    """Paths the index holds as unresolved conflicts (``git diff --diff-filter=U``)."""
    out = git("diff", "--name-only", "--diff-filter=U", cwd=cwd, check=False).stdout or ""
    return [line for line in out.splitlines() if line.strip()]


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
    return (
        f"resolve the conflicts in {', '.join(paths)}, `git add` them, then `git rebase --continue` to finish"
        f"{_walks_on(walk_on)}; `git rebase --abort` goes back to where the rebase started"
    )


def edit_stop_next_step(walk_on: str) -> str:
    """The next step at a commit an edit stopped on: amend, then finish, or finish and walk on with *walk_on*."""
    return f"amend it, then `git rebase --continue` to finish{_walks_on(walk_on)}"


def _then(rerun: str | None) -> str:
    return f", then run {rerun} again" if rerun else " first"


def _quoted(paths: tuple[str, ...]) -> str:
    return " ".join(shlex.quote(path) for path in paths)


def submodule_refusal(
    cwd: Path | str | None, base: str = "HEAD", *, rerun: str | None = None, changes: TrackedChanges | None = None
) -> str | None:
    """Why ger cannot go on over a submodule change ``git stash`` does not save, or ``None``.

    The one wording for both: a submodule a file or symlink replaced in the work tree (a switch
    or rebase would delete that file), and a submodule staged at another commit than *base*
    records. *rerun* names the command to run again; without it, the refusal is a history
    rewrite's. *changes* is :func:`tracked_changes` against *base*, when the caller has it.

    The interactive rebase does not check for a staged submodule before it starts (its own check
    leaves submodules out), so it would start and then stop at its first commit with the rebase
    underway ("your local changes would be overwritten"); an ``edit`` stop it did reach would take
    the staged gitlink into the commit being amended. Neither is saved by ``git stash`` alone, so
    neither can pass as uncommitted changes to stash.

    When *base* is not ``HEAD`` (a staged ``ger review``: the index holds *base*, ``HEAD`` its
    parent), committing would commit the review too, so no commit is advised.
    """
    changes = changes if changes is not None else tracked_changes(cwd, base)
    if paths := changes.replaced_submodules:
        commit = "" if base != "HEAD" else f"commit it (`git add {_quoted(paths)}`, then `git commit`) or "
        return (
            f"a file or symlink replaces the submodule at {', '.join(paths)}; `git stash` does not save it "
            f"and a switch or rebase would delete it: {commit}move it out of the way{_then(rerun)}"
        )
    if not (paths := changes.staged_submodules):
        return None
    source = "" if base == "HEAD" else f" --source={base[:12]}"
    unstage = f"git restore --staged{source} {_quoted(paths)}"
    staged = f"the submodule commit staged for {', '.join(paths)}"
    if base != "HEAD":
        return f"{staged} is not the one {base[:12]} records; `{unstage}`{_then(rerun)}"
    why = "" if rerun else " (a rebase would stop on it, or fold it into the commit it stops at)"
    return f"{staged} is not committed; commit it or `{unstage}`{_then(rerun)}{why}"


def uncommitted_changes_refusal(cwd: Path | str | None, changes: TrackedChanges | None = None) -> str | None:
    """Why rewriting cannot start over uncommitted changes to tracked files, or ``None``.

    What counts is what ``git stash`` would save (:attr:`TrackedChanges.files`): a submodule's
    own state is left out, as git's own check before a rebase leaves it out; a submodule change
    stash does not save is :func:`submodule_refusal`.
    """
    if (changes if changes is not None else tracked_changes(cwd)).files:
        return (
            "you have uncommitted changes; commit them (`ger fix REV` folds them into a stack commit) "
            "or `git stash` them first"
        )
    return None


def history_rewrite_refusal(cwd: Path | str | None) -> str | None:
    """Why rewriting the local stack cannot start here, or ``None``: the one wording every rewrite shares.

    Git refuses an interactive rebase over a rebase underway or uncommitted changes to tracked
    files; asking first replaces git's bare refusal with what to do about it. A submodule change
    stash does not save is refused too: a staged one before git starts a rebase that would stop
    on it, a file replacing one before the rebase deletes that file.
    """
    if refusal := rebase_refusal(cwd):
        return refusal
    changes = tracked_changes(cwd)
    return uncommitted_changes_refusal(cwd, changes) or submodule_refusal(cwd, changes=changes)
