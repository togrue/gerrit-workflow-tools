"""Would a rewrite of the **local stack** conflict? Answered without touching the worktree.

Each pick is one ``git merge-tree`` (the merge ``git rebase`` itself runs), on commits that
exist only in the object store. Needs git 2.40; :class:`PreviewUnavailable` says when it
cannot tell, and the caller falls back on trying the rebase.

:func:`swap_conflicts` asks for every pair at once: all first picks in one
``merge-tree --stdin``, all second picks (onto the bare trees) in another. A git that cannot
batch like that gets the same answer one pick at a time.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from gerrit_workflow_tools.core.git_run import git
from gerrit_workflow_tools.core.stack import (
    Commit,
    StackSnapshot,
    autosquash_order,
    fixup_target_sha,
    pinned_count,
    rewrite_base,
)


# The scratch commits are never reachable from a ref; they only need to be writable.
_IDENT = {
    "GIT_AUTHOR_NAME": "ger preview",
    "GIT_AUTHOR_EMAIL": "ger@localhost",
    "GIT_COMMITTER_NAME": "ger preview",
    "GIT_COMMITTER_EMAIL": "ger@localhost",
}


class PreviewUnavailable(RuntimeError):
    """This git cannot simulate the picks; nothing is known about conflicts."""


class PickStop(str, Enum):
    """Why a rebase stops on a pick."""

    CONFLICT = "conflict"
    EMPTY = "empty"
    """The pick applies but changes nothing: what it changes is there already. ``git rebase -i``
    stops on it (``--empty=stop``) for you to drop it (``--skip``) or keep it empty."""


@dataclass(frozen=True)
class PickConflict:
    """The first commit that does not apply in its new place, and the files it conflicts in."""

    commit: Commit
    files: tuple[str, ...]
    kind: PickStop = PickStop.CONFLICT


def _plumbing(cwd: Path | str, *args: str) -> str:
    done = git(*args, cwd=cwd, env=_IDENT, check=False)
    if done.returncode != 0:
        raise PreviewUnavailable(f"git {args[0]} failed: {done.stderr.strip().partition(chr(10))[0]}")
    return done.stdout.strip()


_TEXT_CONFLICT = re.compile(r"^CONFLICT \((?:content|add/add)\): Merge conflict in (.+)$")


def text_conflicts(cwd: Path | str, onto: str, sha: str) -> tuple[str, ...]:
    """The files picking commit *sha* onto *onto* conflicts in line by line (conflict markers), by the
    merge ``git rebase`` runs; ``()``: none. A rename, mode or delete conflict is not one.

    Raises :class:`PreviewUnavailable` where this git cannot tell.
    """
    done = git("merge-tree", "--write-tree", "--name-only", f"--merge-base={sha}^", onto, sha, cwd=cwd, check=False)
    if done.returncode not in (0, 1) or not done.stdout:
        raise PreviewUnavailable(f"git merge-tree failed: {done.stderr.strip().partition(chr(10))[0]}")
    return tuple(m.group(1) for line in done.stdout.splitlines() if (m := _TEXT_CONFLICT.match(line)))


def _pick(cwd: Path | str, tip: str, sha: str) -> tuple[str | None, tuple[str, ...]]:
    """The tree of commit *sha* picked onto *tip*; or ``None`` and the conflicted files."""
    done = git(
        "merge-tree",
        "--write-tree",
        "--name-only",
        "--no-messages",
        f"--merge-base={sha}^",
        tip,
        sha,
        cwd=cwd,
        check=False,
    )
    lines = done.stdout.splitlines()
    if done.returncode == 0 and lines:
        return lines[0], ()
    # A conflict still writes the tree first. Exit 1 with no tree is git refusing an operand
    # (before 2.45, a bare tree as ours: "expected commit type"), not a conflict.
    if done.returncode == 1 and lines:
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
        tree, files = _pick(cwd, tip, commit.sha)
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


def replay_conflict(
    cwd: Path | str, onto: str, picks: Sequence[Commit], *, onto_tree: str | None = None
) -> PickConflict | None:
    """The first of *picks* (oldest first) the rebase stops on when they are replayed in order onto *onto*.

    What ``git rebase -i <onto>`` would meet: a pick that conflicts, or one that becomes empty
    (its change is upstream already, under another patch id); ``None`` when all apply. *picks*
    are the commits git replays: those it skips as already upstream are left out by the caller.
    One ``merge-tree`` per pick, each onto the tree the one before it left (*onto_tree*, the
    tree of *onto*, saves a ``rev-parse``). A git older than 2.45 takes only commits there:
    the trees are then written as scratch commits, one ``commit-tree`` per pick.
    """
    tip, tip_tree = onto, onto_tree or _plumbing(cwd, "rev-parse", f"{onto}^{{tree}}")
    on_trees = True
    for index, commit in enumerate(picks):
        try:
            tree, files = _pick(cwd, tip, commit.sha)
        except PreviewUnavailable:
            if not on_trees or tip != tip_tree:
                raise
            on_trees = False  # git 2.40-2.44: ours must be a commit
            tip = _plumbing(cwd, "commit-tree", tip_tree, "-m", "ger preview")
            tree, files = _pick(cwd, tip, commit.sha)
        if tree is None:
            return PickConflict(commit, files)
        if tree == tip_tree and not _started_empty(cwd, commit):
            return PickConflict(commit, (), PickStop.EMPTY)
        tip_tree = tree
        if index + 1 < len(picks):
            tip = tree if on_trees else _plumbing(cwd, "commit-tree", tree, "-m", commit.subject)
    return None


def _started_empty(cwd: Path | str, commit: Commit) -> bool:
    """Whether *commit* changes nothing against its parent: ``git rebase`` keeps such a commit, no stop."""
    trees = git("rev-parse", f"{commit.sha}^{{tree}}", f"{commit.sha}^^{{tree}}", cwd=cwd, check=False)
    own, parent = [*trees.stdout.split(), "", ""][:2]
    return trees.returncode == 0 and own == parent


@dataclass(frozen=True)
class FoldConflict:
    """Why a fixup would not fold cleanly into its target with ``git rebase --autosquash``."""

    target: Commit
    stop: PickConflict
    """The pick the autosquash would stop on: the fixup itself, or a commit above the target after it."""
    in_the_way: tuple[Commit, ...]
    """When the fixup itself conflicts: the commits above the target that change the files it conflicts in.
    Its lines were written against what they made of them."""


def fixup_fold_conflict(
    cwd: Path | str, snapshot: StackSnapshot, target: Commit, *, all_tracked: bool
) -> FoldConflict | None:
    """Would the fixup ``ger fix`` is about to make of *target* fold cleanly? ``None`` when it does.

    The fixup is the index (*all_tracked*: every change to tracked files) on the stack's tip.
    Raises :class:`PreviewUnavailable` where it cannot be told: this git, or a target at or below a
    merge, which no rebase replays.
    """
    pinned = pinned_count(cwd, snapshot.commits)
    movable = snapshot.commits[pinned:]
    if target not in movable:
        raise PreviewUnavailable(f"{target.short_sha} is at or below a merge commit")
    fixup = scratch_fixup(cwd, movable[-1].sha, target, all_tracked=all_tracked)
    return fold_conflict(cwd, lambda: rewrite_base(cwd, snapshot, pinned), movable, fixup)


def scratch_fixup(cwd: Path | str, head: str, target: Commit, *, all_tracked: bool) -> Commit:
    """The commit ``git commit --fixup`` of *target* would make of the index on *head*, written but on no ref.

    *all_tracked*: of every change to tracked files (``-a``), as ``git stash create`` saves them.
    """
    if all_tracked:
        saved = _plumbing(cwd, "stash", "create")
        tree = _plumbing(cwd, "rev-parse", f"{saved or head}^{{tree}}")
    else:
        tree = _plumbing(cwd, "write-tree")
    subject = f"fixup! {target.subject}"
    sha = _plumbing(cwd, "commit-tree", tree, "-p", head, "-m", subject)
    return Commit(sha=sha, short_sha=sha[:7], subject=subject, body="", change_id=None)


def fold_conflict(
    cwd: Path | str, base: Callable[[], str], stack: Sequence[Commit], fixup: Commit
) -> FoldConflict | None:
    """Where ``git rebase --autosquash`` would stop with *fixup* (sitting on *stack*'s tip) in the stack.

    ``None`` when it folds cleanly, or when the stack's own fixups stop it anyway: then this one is
    not why. *base* gives the commit *stack* sits on, asked only where the replay starts below the
    target. Raises :class:`PreviewUnavailable` where this git cannot tell.
    """
    commits = [*stack, fixup]
    target = next(c for c in stack if c.sha == fixup_target_sha(commits, fixup.sha))
    order = autosquash_order(commits)
    at = order.index(target)
    if order[:at] == list(stack[:at]):
        # Nothing moves below the target: the autosquash replays what follows it onto it, as it is.
        stop = replay_conflict(cwd, target.sha, [c for c in order[at + 1 :] if c.sha != target.sha])
    else:
        stop = first_conflict(cwd, base(), order, commits)
    if stop is not None and stop.kind is PickStop.EMPTY and stop.commit.sha == fixup.sha:
        stop = None  # the fix is in the target already: it folds into nothing, without a stop
    if stop is None or (stop.commit.sha != fixup.sha and first_conflict(cwd, base(), autosquash_order(stack), stack)):
        return None
    if stop.commit.sha != fixup.sha:
        return FoldConflict(target, stop, (stop.commit,))
    touched = git("log", "--format=%H", f"{target.sha}..{stack[-1].sha}", "--", *stop.files, cwd=cwd, check=False)
    shas = set(touched.stdout.split())
    return FoldConflict(target, stop, tuple(c for c in stack if c.sha in shas))


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
