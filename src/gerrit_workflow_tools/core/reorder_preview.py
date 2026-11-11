"""Would a rewrite of the **local stack** conflict? Answered without touching the worktree.

Each pick is one ``git merge-tree`` (the merge ``git rebase`` itself runs), on commits that
exist only in the object store. Needs git 2.40; :class:`PreviewUnavailable` says when it
cannot tell, and the caller falls back on trying the rebase.

:func:`swap_conflicts` asks for every pair at once: all first picks in one
``merge-tree --stdin``, all second picks (onto the bare trees) in another. A git that cannot
batch like that gets the same answer one pick at a time.
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


def _swapped(commits: Sequence[Commit], i: int) -> list[Commit]:
    return [*commits[:i], commits[i + 1], commits[i], *commits[i + 2 :]]


_Merge = tuple[str, str, str]
"""``(merge base, ours, theirs)``: what one rebase pick hands to the merge."""


def _picks_at_once(cwd: Path | str, merges: Sequence[_Merge]) -> list[tuple[str | None, tuple[str, ...]]] | None:
    """:func:`_pick` for each of *merges*, in one ``git merge-tree --stdin``; ``None`` when this git cannot.

    *ours* may be a bare tree: with the merge base given, git needs no commit there.
    """
    if not merges:
        return []
    done = git(
        "merge-tree",
        "--stdin",
        "--name-only",
        "--no-messages",
        cwd=cwd,
        check=False,
        input="".join(f"{base} -- {ours} {theirs}\n" for base, ours, theirs in merges),
    )
    if done.returncode != 0:
        return None
    # Per merge: status NUL tree NUL (conflicted file NUL)* NUL; status is 1 when clean.
    fields = iter(done.stdout.split("\0"))
    answers: list[tuple[str | None, tuple[str, ...]]] = []
    for _ in merges:
        clean, tree = next(fields, ""), next(fields, "")
        files = tuple(dict.fromkeys(iter(lambda: next(fields, ""), "")))
        if clean not in ("0", "1") or not tree:
            return None
        answers.append((tree, ()) if clean == "1" else (None, files))
    return answers


def swap_conflicts(cwd: Path | str, base: str, stack: Sequence[Commit]) -> list[PickConflict | None]:
    """For each adjacent pair of *stack* (oldest first): the conflict of swapping the two, or ``None``.

    Swapping *lower* and *upper* picks *upper* onto what sat below *lower*, then *lower* onto
    that. When the result is *upper*'s own tree, the rest of the stack replays unchanged.
    """
    commits = list(stack)
    pairs = list(range(len(commits) - 1))
    below = [base, *(c.sha for c in commits)]
    firsts = _picks_at_once(cwd, [(commits[i].sha, below[i], commits[i + 1].sha) for i in pairs])
    if firsts is None:
        return [first_conflict(cwd, base, _swapped(commits, i), commits) for i in pairs]
    answers: list[PickConflict | None] = [
        None if tree else PickConflict(commits[i + 1], files) for i, (tree, files) in zip(pairs, firsts, strict=True)
    ]
    clean = [i for i in pairs if firsts[i][0]]
    seconds = _picks_at_once(cwd, [(below[i], str(firsts[i][0]), commits[i].sha) for i in clean])
    if seconds is None:
        return [
            answers[i] if not firsts[i][0] else first_conflict(cwd, base, _swapped(commits, i), commits) for i in pairs
        ]
    differ = []
    for i, (tree, files) in zip(clean, seconds, strict=True):
        if tree is None:
            answers[i] = PickConflict(commits[i], files)
        elif i + 2 < len(commits):
            differ.append((i, tree))
    if differ:
        own = _plumbing(cwd, "rev-parse", *(f"{commits[i + 1].sha}^{{tree}}" for i, _ in differ)).split()
        for (i, tree), upper_tree in zip(differ, own, strict=True):
            if tree != upper_tree:  # the rest replays onto other content: follow it up the stack
                answers[i] = first_conflict(cwd, base, _swapped(commits, i), commits)
    return answers
