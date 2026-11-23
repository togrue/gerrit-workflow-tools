"""A rebase underway: how far it got, the refusals and the next step.

Every history-moving ger command asks here whether a rebase is underway before it starts, and
every ger-started rebase that stops on a conflict ends with :func:`conflict_next_step`, so each
reason has one wording. Why a rewrite cannot start over your changes is
:mod:`gerrit_workflow_tools.core.rewrite_refusal`.
Which branch a stopped rebase belongs to stays with branch resolution in
:mod:`gerrit_workflow_tools.core.git_state`; the local stack while it runs is
:mod:`gerrit_workflow_tools.core.rebasing_stack`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from gerrit_workflow_tools.core.git_run import git
from gerrit_workflow_tools.core.git_state import am_underway, git_dir, rebase_underway
from gerrit_workflow_tools.core.tracked_changes import tracked_changes


def rebase_in_progress(cwd: Path | str | None) -> bool:
    """True while git has a rebase underway, including one stopped at a commit; a ``git am`` is not one."""
    return rebase_underway(git_dir(cwd))


def am_in_progress(cwd: Path | str | None) -> bool:
    """True while a ``git am`` is underway (:func:`am_underway`)."""
    return am_underway(git_dir(cwd))


class RebaseStop(Enum):
    """Why a rebase underway is stopped: what the next step is, in core's words, not a shell's."""

    EDIT = "edit"
    """Stopped with the commit it is at applied whole: amend it, then continue.

    An ``edit``, or a conflict whose resolution was committed by hand (``git commit``, not
    ``--continue``): either way ``HEAD`` is the commit, or an amend of it.
    """
    SPLIT = "split"
    """Stopped at a commit applied whole, and ``HEAD`` has moved on from it: the commits above it,
    or above its parent once it was reset away, were made at the stop (a split). Continue goes on
    from ``HEAD``."""
    CONFLICT = "conflict"
    """Stopped on a conflict: :attr:`RebaseProgress.unmerged` names the paths still unresolved."""
    RESOLVED = "resolved"
    """Stopped on a conflict that is resolved and added: ready to continue."""
    PAUSED = "paused"
    """Stopped between commits (``exec``, ``break``)."""


class RebaseAction(Enum):
    """A way on from where a rebase underway stopped: ``git rebase --<action>``.

    Not :class:`~gerrit_workflow_tools.core.rebasing_stack.RebaseStep`, where it stands on a commit.
    """

    CONTINUE = "continue"
    ABORT = "abort"
    SKIP = "skip"


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
    """Paths the index still holds as unresolved conflicts, at whatever stop."""
    keeps_todo: bool = True
    """``False`` for a rebase git runs through ``am`` (``rebase-apply``): it says nothing of its commits."""
    amend: str | None = None
    """``HEAD`` where git stopped with :attr:`current` applied whole (``amend``), else ``None``."""
    settled: frozenset[str] = frozenset()
    """Ids ``HEAD`` may be at where git itself left it: ``onto``, the commits done as they were
    (picked unchanged) and as it rewrote them, and :attr:`amend`. Telling a commit made by hand
    at the stop from one git made needs no git call."""
    moved: bool = False
    """``HEAD`` has moved on from where git stopped: commits were made at the stop, or reset away."""
    clean: bool = False
    """The index and work tree hold nothing ``git rebase --skip`` would throw away; told only
    where the commit it stopped at was committed by hand (:attr:`committed_by_hand`)."""

    @property
    def committed_by_hand(self) -> bool:
        """Stopped on a conflict, and ``HEAD`` moved off where git stopped: a commit made at the stop.

        Where that commit is the one it stopped at, the rebasing stack tells so, and the stop is an
        ``edit`` (:func:`~gerrit_workflow_tools.core.rebasing_stack.stopped_rebase`).
        """
        return self.moved and not self.applied

    @property
    def stop(self) -> RebaseStop | None:
        """Why the rebase is stopped (:class:`RebaseStop`); ``None`` when that cannot be told.

        A rebase that keeps no todo stops only on a conflict; once ``HEAD`` moved off where it
        stopped, what ``--continue`` would do cannot be told from its state.
        """
        if self.current is None:
            return RebaseStop.PAUSED if self.keeps_todo else None
        if self.moved:
            return RebaseStop.SPLIT if self.applied else None
        if self.applied:
            return RebaseStop.EDIT
        return RebaseStop.CONFLICT if self.unmerged else RebaseStop.RESOLVED

    @property
    def actions(self) -> tuple[RebaseAction, ...]:
        """The ways on from this stop, by one verdict for ``ger rebase``, ``ger edit -n`` and ``ger serve``.

        Abort always. Continue unless paths are still unmerged. Skip at a stop on a conflict
        (resolved or not), where it leaves out the commit not applied yet: at any other stop the
        commit is already in, and ``git rebase --skip`` would only throw away what is not committed.
        Where the conflict's resolution was committed by hand as another commit, Skip goes on from
        ``HEAD`` once nothing is left to throw away; then a rebase through ``am`` has nothing left
        to continue with (git would say "No changes"), so Skip is its only way on.
        """
        settled_by_hand = self.committed_by_hand and self.clean
        go_on = () if self.unmerged or (settled_by_hand and not self.keeps_todo) else (RebaseAction.CONTINUE,)
        on_conflict = self.stop in (RebaseStop.CONFLICT, RebaseStop.RESOLVED)
        skip = (RebaseAction.SKIP,) if on_conflict or settled_by_hand else ()
        return (*go_on, RebaseAction.ABORT, *skip)

    def refusal(self, action: RebaseAction, *, walk_on: str | None = None) -> str | None:
        """Why *action* is not a way on from this stop (:attr:`actions`), or ``None`` when it is.

        Told from the stop alone; ``ger rebase`` also refuses to continue over unstaged changes
        (:func:`go_on_refusal`). *walk_on* names the command that also walks on, where a walk is underway.
        """
        if action in self.actions:
            return None
        if action is RebaseAction.CONTINUE:
            if self.unmerged:
                return f"the index has unresolved conflicts; {_resolve_step(self.unmerged, walk_on)}"
            return (
                "the commit it stopped on was committed by hand, so nothing is left to apply and "
                "`git rebase --continue` would stop again: `git rebase --skip` goes on from HEAD, "
                "or `git rebase --abort`"
            )
        if self.committed_by_hand:
            return (
                "--skip leaves out the commit the rebase stopped on, but it was committed by hand "
                "(HEAD has moved on from where it stopped): `git rebase --skip` would keep that commit and "
                "throw away your uncommitted changes; commit them or `git stash` them first, "
                "or `git rebase --abort`"
            )
        why = _SKIP_REFUSALS.get(self.stop) if self.stop else None
        return (
            f"--skip leaves out a commit the rebase stopped on with a conflict, but "
            f"{why or 'ger cannot tell where it stopped'}: "
            "`git rebase --skip` would only throw away your uncommitted changes; "
            "go on with `git rebase --continue`, or `git rebase --abort`"
        )


_FOLDING = frozenset({"squash", "s", "fixup", "f"})
_REPLAYING = frozenset({"pick", "p", "reword", "r", "edit", "e"}) | _FOLDING


def _todo_lines(path: Path) -> list[tuple[str, str | None]]:
    """One ``(command, commit)`` per command line of a rebase todo file; ``None`` for a commit it does not replay."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    entries: list[tuple[str, str | None]] = []
    for line in lines:
        words = line.split()
        if not words or words[0].startswith("#"):
            continue
        args = [word for word in words[1:] if not word.startswith("-")]
        entries.append((words[0], args[0].lower() if words[0] in _REPLAYING and args else None))
    return entries


def _todo_ids(path: Path) -> list[str | None]:
    """One entry per command line of a rebase todo file: the commit it replays, or ``None``."""
    return [sha for _, sha in _todo_lines(path)]


def _state_word(path: Path) -> str | None:
    """The one line git wrote into a rebase state file, or ``None``."""
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _rebased_branch(state: Path) -> str | None:
    """The branch a rebase's *state* dir says it rebases (``head-name``); ``None`` for a detached HEAD."""
    head_name = _state_word(state / "head-name") or ""
    return head_name.removeprefix("refs/heads/") if head_name.startswith("refs/heads/") else None


def _rewritten(path: Path) -> list[str]:
    """The new ids in ``rewritten-list`` (``<old> <new>`` per commit git replayed with a new id)."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [words[1].lower() for line in lines if len(words := line.split()) >= 2]


def rebase_progress(cwd: Path | str | None, *, repo_dir: Path | None = None) -> RebaseProgress | None:
    """How far the rebase underway got, or ``None`` when there is none (a ``git am`` is not one).

    Read from git's own state files; one ``git diff`` for the unmerged paths, and where ``HEAD``
    moved off a commit not applied (:attr:`RebaseProgress.committed_by_hand`) two more to tell
    whether anything is left uncommitted. A rebase git runs through ``am`` (``rebase-apply``)
    keeps no todo: it answers its branch, the commit it stopped at and the unmerged paths.
    *repo_dir* is the git dir when the caller already knows it (``run.worktree.repository_dir``).
    """
    repo_dir = repo_dir if repo_dir is not None else git_dir(cwd)
    if repo_dir is None or not rebase_underway(repo_dir):
        return None
    state = repo_dir / "rebase-merge"
    if not state.exists():
        return _apply_progress(cwd, repo_dir)
    done_lines = _todo_lines(state / "done")
    done = [sha for _, sha in done_lines]
    current = done[-1] if done else None
    amend = _state_word(state / "amend")
    # A stop with the commit in hand leaves REBASE_HEAD; `amend` is there only when it was applied whole,
    # except that a squash or fixup stopped on a conflict writes `amend` too: the commit it folds into.
    folding = bool(done_lines) and done_lines[-1][0] in _FOLDING
    applied = current is None or not (repo_dir / "REBASE_HEAD").exists() or (amend is not None and not folding)
    onto = _state_word(state / "onto")
    settled = frozenset(sha for sha in (*done[:-1], *_rewritten(state / "rewritten-list"), onto, amend) if sha)
    # Detached while it runs, so HEAD names a commit: one git did not leave there was made by hand.
    moved = not applied and _state_word(repo_dir / "HEAD") not in settled
    return RebaseProgress(
        done=tuple(sha for sha in done[:-1] if sha),
        current=current,
        applied=applied,
        todo=tuple(sha for sha in _todo_ids(state / "git-rebase-todo") if sha),
        branch=_rebased_branch(state),
        onto=onto,
        orig_head=_state_word(state / "orig-head"),
        unmerged=tuple(unmerged_paths(cwd)),
        amend=amend,
        settled=settled,
        moved=moved,
        clean=moved and _nothing_uncommitted(cwd),
    )


def _apply_progress(cwd: Path | str | None, repo_dir: Path) -> RebaseProgress:
    """:func:`rebase_progress` of a rebase git runs through ``am``: it stops only on a conflict.

    ``original-commit`` names the commit it stopped at, ``abort-safety`` where ``HEAD`` was then.
    """
    state = repo_dir / "rebase-apply"
    current = _state_word(state / "original-commit")
    stopped_at = _state_word(state / "abort-safety")
    moved = stopped_at is not None and stopped_at != _state_word(repo_dir / "HEAD")
    return RebaseProgress(
        current=current,
        applied=current is None,
        branch=_rebased_branch(state),
        onto=_state_word(state / "onto"),
        orig_head=_state_word(state / "orig-head"),
        unmerged=tuple(unmerged_paths(cwd)),
        keeps_todo=False,
        moved=moved,
        clean=moved and current is not None and _nothing_uncommitted(cwd),
    )


def _nothing_uncommitted(cwd: Path | str | None) -> bool:
    """Whether ``git rebase --skip`` would throw nothing away: no tracked change, no submodule staged or replaced."""
    changes = tracked_changes(cwd)
    return not (changes.files or changes.staged_submodules or changes.replaced_submodules)


AM_UNDERWAY = "a `git am` is in progress; finish it (`git am --continue`) or `git am --abort`"
"""What is underway while ``git am`` applies patches: every refusal over it starts so."""


def rebase_refusal(cwd: Path | str | None) -> str | None:
    """Why a command that moves branches cannot start during a rebase or a ``git am``, or ``None``."""
    repo_dir = git_dir(cwd)
    if rebase_underway(repo_dir):
        return "a rebase is already in progress; run `git rebase --continue` or `git rebase --abort` first"
    if am_underway(repo_dir):
        return f"{AM_UNDERWAY} first"
    return None


def unmerged_paths(cwd: Path | str | None) -> list[str]:
    """Paths the index holds as unresolved conflicts (``git diff --diff-filter=U``), as they are named.

    Read NUL-delimited (``-z``), so a name with a non-ASCII letter, a tab or a quote is the literal
    path, not git's C-quoted ``"caf\\303\\251.txt"``: callers open it and show it.
    """
    return _nul_paths(git("diff", "--name-only", "-z", "--diff-filter=U", cwd=cwd, check=False).stdout)


def _nul_paths(out: str | None) -> list[str]:
    """The paths of a ``--name-only -z`` listing."""
    return [path for path in (out or "").split("\0") if path]


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


def _unstaged_paths(cwd: Path | str | None) -> list[str]:
    """Tracked paths whose work tree differs from the index, a submodule's own state left out: git's own check.

    ``git rebase --continue`` refreshes the index first, then asks ``diff-files``, so a file only
    touched, or changed only in line endings ``core.autocrlf`` takes back, is no change.
    """
    git("update-index", "-q", "--refresh", cwd=cwd, check=False)
    return _nul_paths(git("diff-files", "--name-only", "-z", "--ignore-submodules", cwd=cwd, check=False).stdout)


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
    server go by one verdict (:meth:`RebaseProgress.refusal`). Beyond it: git refuses to continue
    over unstaged changes to tracked files with "You must edit all merge conflicts", whatever the
    stop; this says what to do instead. *walk_on* names the command that also walks on, where a
    walk is underway.
    """
    if progress is None:
        return f"no rebase is in progress: {AM_UNDERWAY}" if am_in_progress(cwd) else "no rebase is in progress"
    if refusal := progress.refusal(action, walk_on=walk_on):
        return refusal
    if action is RebaseAction.CONTINUE and (paths := _unstaged_paths(cwd)):
        on_conflict = progress.stop in (RebaseStop.CONFLICT, RebaseStop.RESOLVED)
        take = "`git add` them" if on_conflict else "amend them in (`git commit --amend -a`)"
        return f"you have unstaged changes to {', '.join(paths)}; {take} or `git stash` them, then continue"
    return None


def edit_stop_next_step(walk_on: str) -> str:
    """The next step at a commit an edit stopped on: amend, then finish, or finish and walk on with *walk_on*."""
    return f"amend it, then `git rebase --continue` to finish{_walks_on(walk_on)}"
