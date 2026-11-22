"""How far the working branch's upstream moved on past the **local stack**, and what rebasing onto it would do.

After a ``git fetch``, ``origin/main`` may have commits the branch has not: one
``rev-list --count`` says how many. Only then is the rebase onto it previewed, as the
reorder preview does it (``git merge-tree``, worktree untouched): each commit of the stack
replayed in order onto the new upstream, one merge per commit, until one would conflict.
Nothing is said during a rebase underway: that one is finished first.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.git_run import git
from gerrit_workflow_tools.core.git_state import configured_upstream_abbrev
from gerrit_workflow_tools.core.reorder_preview import PickConflict, PreviewUnavailable, replay_conflict
from gerrit_workflow_tools.core.stack import Commit, merge_commits


@dataclass(frozen=True)
class UpstreamMoved:
    """The working branch's upstream, how many commits it has that the branch has not, and the rebase onto it."""

    name: str
    """The upstream as the user names it (``origin/main``): what ``ger rebase <name>`` rebases onto."""
    behind: int
    conflict: PickConflict | None = None
    """The first stack commit that would not apply on the upstream as it is now; ``None`` when all do."""
    preview: bool = True
    """``False`` when the rebase could not be simulated (git older than 2.40, a merge in the stack):
    :attr:`conflict` is then not known, and only the rebase itself finds out."""

    def advice(self) -> str:
        """What to do about it, in words: ``origin/main has 1 new commit; rebase onto it with …``."""
        said = f"{self.name} has {self.behind} new commit{'' if self.behind == 1 else 's'}"
        hit = self.conflict
        where = f" in {', '.join(hit.files)}" if hit and hit.files else ""
        conflict = f'; "{hit.commit.subject}" would conflict{where}' if hit else ""
        return f"{said}; rebase onto it with `ger rebase {self.name}`{conflict}"


def upstream_moved(
    cwd: Path | str, branch: str | None, commits: Sequence[Commit], *, settings: Settings
) -> UpstreamMoved | None:
    """*branch*'s upstream and how far it moved on past *commits* (the local stack, oldest first).

    ``None`` without a branch or an upstream. One ``git rev-list --count --left-right``; when the upstream
    has moved on, also one ``git merge-tree`` per commit replayed (and one ``rev-list`` for
    merges), stopping at the first that would conflict.
    """
    if branch is None:
        return None
    # Both sides at once, as ``git status`` counts: ``branch..upstream`` alone miscounts when
    # commit dates are skewed (a rebase with an old committer date), the walk stopping too soon.
    counted = git("rev-list", "--count", "--left-right", f"{branch}...{branch}@{{upstream}}", cwd=cwd, check=False)
    if counted.returncode != 0:
        return None
    name = configured_upstream_abbrev(settings, branch) or _upstream_name(cwd, branch)
    behind = int(counted.stdout.split()[-1])
    if not behind or not commits:
        return UpstreamMoved(name, behind)
    if merge_commits(cwd, [c.sha for c in commits]):
        return UpstreamMoved(name, behind, preview=False)  # git rebase does not replay a merge: no telling
    try:
        conflict = replay_conflict(cwd, f"{branch}@{{upstream}}", commits)
    except PreviewUnavailable:
        return UpstreamMoved(name, behind, preview=False)
    return UpstreamMoved(name, behind, conflict)


def _upstream_name(cwd: Path | str, branch: str) -> str:
    """The upstream's short name when the branch config does not spell it as a remote branch."""
    named = git("rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}", cwd=cwd, check=False)
    return named.stdout.strip() or f"{branch}@{{upstream}}"
