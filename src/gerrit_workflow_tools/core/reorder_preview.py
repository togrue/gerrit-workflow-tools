"""Would a rewrite of the **local stack** conflict? Answered without touching the worktree.

Each pick is one ``git merge-tree`` (the merge ``git rebase`` itself runs), on commits that
exist only in the object store. Needs git 2.40; :class:`PreviewUnavailable` says when it
cannot tell, and the caller falls back on trying the rebase.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from gerrit_workflow_tools.core.git_run import git
from gerrit_workflow_tools.core.stack import Commit


# The scratch commits are never reachable from a ref; they only need to be writable.
_IDENT = {
    "GIT_AUTHOR_NAME": "ger preview",
    "GIT_AUTHOR_EMAIL": "ger@localhost",
    "GIT_COMMITTER_NAME": "ger preview",
    "GIT_COMMITTER_EMAIL": "ger@localhost",
}


class PreviewUnavailable(RuntimeError):
    """This git cannot simulate the picks; nothing is known about conflicts."""


@dataclass(frozen=True)
class PickConflict:
    """The first commit that does not apply in its new place, and the files it conflicts in."""

    commit: Commit
    files: tuple[str, ...]


def _plumbing(cwd: Path | str, *args: str) -> str:
    done = git(*args, cwd=cwd, env=_IDENT, check=False)
    if done.returncode != 0:
        raise PreviewUnavailable(f"git {args[0]} failed: {done.stderr.strip().partition(chr(10))[0]}")
    return done.stdout.strip()


def _pick(cwd: Path | str, tip: str, commit: Commit) -> tuple[str | None, tuple[str, ...]]:
    """The tree of *commit* picked onto *tip*; or ``None`` and the conflicted files."""
    done = git(
        "merge-tree",
        "--write-tree",
        "--name-only",
        "--no-messages",
        f"--merge-base={commit.sha}^",
        tip,
        commit.sha,
        cwd=cwd,
        check=False,
    )
    lines = done.stdout.splitlines()
    if done.returncode == 0 and lines:
        return lines[0], ()
    if done.returncode == 1:
        return None, tuple(dict.fromkeys(line for line in lines[1:] if line))
    raise PreviewUnavailable(f"git merge-tree failed: {done.stderr.strip().partition(chr(10))[0]}")


def first_conflict(cwd: Path | str, base: str, picks: Sequence[Commit], stack: Sequence[Commit]) -> PickConflict | None:
    """The first of *picks* (oldest first) that conflicts when replayed onto *base*; ``None`` when all apply.

    *stack* is the local stack as it is, sitting on *base*. *picks* are its commits in the
    order wanted, dropped ones left out; a fold applies like a pick.
    """
    shas = [c.sha for c in stack]
    parent = dict(zip(shas, [base, *shas[:-1]], strict=True))
    tip = base
    for index, commit in enumerate(picks):
        if parent.get(commit.sha) == tip:
            tip = commit.sha  # where it already is: nothing to replay
            continue
        tree, files = _pick(cwd, tip, commit)
        if tree is None:
            return PickConflict(commit, files)
        rest = list(picks[index + 1 :])
        if not rest:
            return None
        tail = list(stack[len(stack) - len(rest) :])
        below = stack[len(stack) - len(rest) - 1].sha if len(rest) < len(stack) else None
        if below and rest == tail and tree == _plumbing(cwd, "rev-parse", f"{below}^{{tree}}"):
            return None  # same content as the untouched tail sat on before: it replays as it is
        tip = _plumbing(cwd, "commit-tree", tree, "-p", tip, "-m", commit.subject)
    return None


def swap_conflicts(cwd: Path | str, base: str, stack: Sequence[Commit]) -> list[PickConflict | None]:
    """For each adjacent pair of *stack* (oldest first): the conflict of swapping the two, or ``None``."""
    commits = list(stack)
    return [
        first_conflict(cwd, base, [*commits[:i], commits[i + 1], commits[i], *commits[i + 2 :]], commits)
        for i in range(len(commits) - 1)
    ]
