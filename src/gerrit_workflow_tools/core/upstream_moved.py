"""How far the working branch's upstream moved on past the **local stack**, and what rebasing onto it would do.

After a ``git fetch``, ``origin/main`` may have commits the branch has not: one
``rev-list --count`` says how many. Only then is the rebase onto it previewed, as the
reorder preview does it (``git merge-tree``, worktree untouched): the commits ``git rebase``
would replay, chosen as it chooses them, replayed in order onto the new upstream, one merge
per commit, until one would stop the rebase. Nothing is said during a rebase underway: that
one is finished first.

The preview is simulated anew on every call: what a merge does also depends on attributes
(``.git/info/attributes``, ``.gitattributes``, ``core.attributesFile``) and merge drivers, which
change without any object id changing, so no key for a process-wide memo is complete (ADR-0014).
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from pathlib import Path

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit_change_status import LogCommit, PatchsetStatus
from gerrit_workflow_tools.core.git_run import git
from gerrit_workflow_tools.core.git_state import configured_upstream_abbrev
from gerrit_workflow_tools.core.reorder_preview import PickConflict, PickStop, PreviewUnavailable, replay_conflict
from gerrit_workflow_tools.core.stack import Commit


DROP_MERGED = "--drop-merged-equivalent"


@dataclass(frozen=True)
class UpstreamMoved:
    """The working branch's upstream, how many commits it has that the branch has not, and the rebase onto it."""

    name: str
    """The upstream as the user names it (``origin/main``): what ``ger rebase <name>`` rebases onto."""
    behind: int
    conflict: PickConflict | None = None
    """The first stack commit the rebase would stop on, on the upstream as it is now: one that conflicts,
    or one that becomes empty (:attr:`PickConflict.kind`); ``None`` when all apply."""
    preview: bool = True
    """``False`` when the rebase could not be simulated (a git older than 2.40, a merge in the stack):
    :attr:`conflict` is then not known, and only the rebase itself finds out; :attr:`reason` says why."""
    reason: str | None = None
    """Why there is no preview, in words; ``None`` when there is one."""
    dropped: tuple[Commit, ...] = ()
    """The stack commits the rebase leaves out, so the preview does too: already upstream (the same
    patch, as ``git rebase`` sees it), or merged on Gerrit with the same code (dropped by :data:`DROP_MERGED`)."""
    drop_merged: bool = False
    """Whether a commit of the stack is merged on Gerrit with the same code: :attr:`argv` then drops it."""

    @property
    def argv(self) -> tuple[str, ...]:
        """The one ger command that rebases onto it, ``ger`` left out: ``rebase origin/main [DROP_MERGED]``."""
        return ("rebase", self.name, *((DROP_MERGED,) if self.drop_merged else ()))

    def advice(self) -> str:
        """What to do about it, in words: ``origin/main has 1 new commit; rebase onto it with …``."""
        said = f"{self.name} has {self.behind} new commit{'' if self.behind == 1 else 's'}"
        hit = self.conflict
        if hit is None:
            stop = ""
        elif hit.kind is PickStop.EMPTY:
            stop = f'; "{hit.commit.subject}" would become empty: its change is already upstream'
        else:
            where = f" in {', '.join(hit.files)}" if hit.files else ""
            stop = f'; "{hit.commit.subject}" would conflict{where}'
        n = len(self.dropped)
        dropped = f"; {n} commit{' is' if n == 1 else 's are'} already upstream and dropped" if n else ""
        return f"{said}; rebase onto it with `ger {' '.join(self.argv)}`{stop}{dropped}"


def merged_with_same_code(commit: LogCommit) -> bool:
    """Whether *commit* is merged on Gerrit with the same code, proved: what :data:`DROP_MERGED` drops."""
    return commit.patchset_status == PatchsetStatus.MERGED_SAME and commit.merged_equivalent is True


def merged_same(commits: Sequence[LogCommit]) -> frozenset[str]:
    """The shas of *commits* that ``ger rebase --drop-merged-equivalent`` drops: merged, the same code proved."""
    return frozenset(c.sha for c in commits if merged_with_same_code(c))


def upstream_moved(
    cwd: Path | str,
    branch: str | None,
    commits: Sequence[Commit],
    *,
    settings: Settings,
    merged: Collection[str] = frozenset(),
) -> UpstreamMoved | None:
    """*branch*'s upstream and how far it moved on past *commits* (the local stack, oldest first).

    *merged* are the shas of *commits* merged on Gerrit with the same code (:func:`merged_same`, from
    the **Gerrit overlay**): the rebase drops them, and so does the preview. ``None`` without a branch or
    an upstream. One ``git rev-list --count --left-right``; when the upstream has moved on, also one
    ``git rev-parse`` of it, one ``git rev-list --cherry-pick`` for the commits
    git would replay and one ``git merge-tree`` per commit replayed, up to the first that would stop.
    """
    if branch is None:
        return None
    # Both sides at once, as ``git status`` counts: ``branch..upstream`` alone miscounts when
    # commit dates are skewed (a rebase with an old committer date), the walk stopping too soon.
    # The branch spelled in full: a tag of the same name would win over it.
    local, upstream = f"refs/heads/{branch}", f"{branch}@{{upstream}}"
    counted = git("rev-list", "--count", "--left-right", f"{local}...{upstream}", cwd=cwd, check=False)
    if counted.returncode != 0:
        return None
    name = configured_upstream_abbrev(settings, branch) or _upstream_name(cwd, branch)
    behind = int(counted.stdout.split()[-1])
    drop = frozenset(c.sha for c in commits if c.sha in merged)
    if not behind or not commits:
        return UpstreamMoved(name, behind, drop_merged=bool(drop))
    tips = git("rev-parse", upstream, f"{upstream}^{{tree}}", cwd=cwd, check=False)
    onto, onto_tree = [*tips.stdout.split(), "", ""][:2]
    if tips.returncode != 0 or not onto_tree:
        return UpstreamMoved(name, behind, preview=False, reason=f"{name} cannot be read", drop_merged=bool(drop))
    replay = _replay(cwd, onto, onto_tree, commits, drop)
    return UpstreamMoved(name, behind, *replay, drop_merged=bool(drop))


_Replay = tuple[PickConflict | None, bool, str | None, tuple[Commit, ...]]
"""``(conflict, preview, reason, dropped)`` of :class:`UpstreamMoved`."""


def _replay(cwd: Path | str, onto: str, onto_tree: str, commits: Sequence[Commit], drop: frozenset[str]) -> _Replay:
    """The rebase of *commits* onto *onto*, previewed; *drop* are the shas it leaves out."""
    # The commits git replays, as ``git rebase`` chooses them: not those whose patch is upstream already.
    listed = git(
        "rev-list", "--cherry-pick", "--right-only", "--parents", f"{onto}...{commits[-1].sha}", cwd=cwd, check=False
    )
    if listed.returncode != 0:
        return None, False, f"git rev-list failed: {listed.stderr.strip().partition(chr(10))[0]}", ()
    lines = [line.split() for line in listed.stdout.splitlines() if line]
    if any(len(line) > 2 for line in lines):
        return None, False, "a merge in the stack: git rebase does not replay it", ()
    replayed = {line[0] for line in lines} - drop
    picks = [c for c in commits if c.sha in replayed]
    dropped = tuple(c for c in commits if c.sha not in replayed)
    try:
        return replay_conflict(cwd, onto, picks, onto_tree=onto_tree), True, None, dropped
    except PreviewUnavailable as error:
        return None, False, f"this git cannot simulate it ({error})", dropped


def _upstream_name(cwd: Path | str, branch: str) -> str:
    """The upstream's short name when the branch config does not spell it as a remote branch."""
    named = git("rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}", cwd=cwd, check=False)
    return named.stdout.strip() or f"{branch}@{{upstream}}"
