"""A rebase underway and what it means for the next command: the refusals and the next step.

Every history-moving ger command asks here before it starts, and every ger-started rebase that
stops on a conflict ends with :func:`conflict_next_step`, so each reason has one wording.
Which branch a stopped rebase belongs to stays with branch resolution in
:mod:`gerrit_workflow_tools.core.git_state`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gerrit_workflow_tools.core.git_run import git, git_out
from gerrit_workflow_tools.core.git_state import git_dir


def rebase_in_progress(cwd: Path | str | None) -> bool:
    """True while git has a rebase underway, including one stopped at a commit."""
    repo_dir = git_dir(cwd)
    return repo_dir is not None and any((repo_dir / d).exists() for d in ("rebase-merge", "rebase-apply"))


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

    def state_of(self, sha: str) -> str | None:
        """``done``, ``current`` or ``todo`` for a commit the rebase replays, else ``None``."""

        def named(ids: tuple[str, ...]) -> bool:
            return any(sha.startswith(known) for known in ids if known)

        if self.current and sha.startswith(self.current):
            return "current"
        if named(self.done):
            return "done"
        return "todo" if named(self.todo) else None


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


def rebase_progress(cwd: Path | str | None) -> RebaseProgress | None:
    """How far the rebase underway got, or ``None`` when there is none.

    Read from git's own state files, no subprocess. A rebase git runs through ``am``
    (``rebase-apply``) keeps no todo: it answers an empty progress.
    """
    repo_dir = git_dir(cwd)
    if repo_dir is None or not rebase_in_progress(cwd):
        return None
    state = repo_dir / "rebase-merge"
    done = _todo_ids(state / "done")
    current = done[-1] if done else None
    # A stop with the commit in hand leaves REBASE_HEAD; `amend` is there only when it was applied whole.
    applied = current is None or not (repo_dir / "REBASE_HEAD").exists() or (state / "amend").exists()
    return RebaseProgress(
        done=tuple(sha for sha in done[:-1] if sha),
        current=current,
        applied=applied,
        todo=tuple(sha for sha in _todo_ids(state / "git-rebase-todo") if sha),
    )


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


def history_rewrite_refusal(cwd: Path | str | None) -> str | None:
    """Why rewriting the local stack cannot start here, or ``None``: the one wording every rewrite shares.

    Git refuses an interactive rebase over a rebase underway or uncommitted changes to tracked
    files; asking first replaces git's bare refusal with what to do about it.
    """
    if refusal := rebase_refusal(cwd):
        return refusal
    if git_out("status", "--porcelain", "--untracked-files=no", cwd=cwd):
        return (
            "you have uncommitted changes; commit them (`ger fix REV` folds them into a stack commit) "
            "or `git stash` them first"
        )
    return None
