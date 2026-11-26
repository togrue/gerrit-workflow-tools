"""What ``git rebase --abort`` throws away at the stop a rebase is at, and what ``ger rebase --abort`` keeps.

git's abort puts the branch back where it was and resets the work tree to it. Two kinds of work
go with that: changes to tracked files not committed at a stop with the commit applied (a fix
saved, not amended), and the commits the rebase already made that differ from the branch before
in content or message (an amend, a conflict resolved, a commit split at a stop). A commit only
replayed onto a new base is no work: the branch still has it.

The first is kept in a stash before git runs (:func:`keep_uncommitted`), so it is got back in one
step; the second stays reachable by its id for as long as git keeps its reflog, so a client asks
before it aborts over it. At a stop on a conflict, the files the conflict touched are the
commit's own half-applied change and the resolution in progress: nothing is stashed there.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gerrit_workflow_tools.core.git_run import git, git_out
from gerrit_workflow_tools.core.rebase_state import RebaseProgress, RebaseStop
from gerrit_workflow_tools.core.tracked_changes import tracked_changes


@dataclass(frozen=True)
class RewrittenCommit:
    """A commit the rebase made that the branch before it does not have in that form."""

    sha: str
    short_sha: str
    subject: str


@dataclass(frozen=True)
class AbortLoss:
    """What an abort from this stop would take off the work tree and the branch."""

    uncommitted: tuple[str, ...] = ()
    """Tracked files changed and not committed at a stop with the commit applied: ``ger rebase
    --abort`` keeps them in a stash."""
    rewritten: tuple[RewrittenCommit, ...] = ()
    """Commits made in this rebase whose change or message the branch before it does not have:
    gone from the branch after an abort, reachable by id while git keeps its reflog."""
    resolution: bool = False
    """Stopped on a conflict that is resolved and added: the resolution goes with an abort."""

    @property
    def asks(self) -> bool:
        """Whether an abort throws away what no stash keeps: a client asks first then."""
        return bool(self.rewritten) or self.resolution


def _changes(cwd: Path | str | None, rev_range: str) -> dict[str, tuple[str, str]]:
    """``(patch id, message)`` by commit of *rev_range*, first parent: a commit's change in a form a replay keeps."""
    messages: dict[str, str] = {}
    for record in git_out("log", "--first-parent", "--format=%H%x00%B%x01", rev_range, cwd=cwd).split("\x01"):
        sha, _, body = record.strip("\n").partition("\x00")
        if sha:
            messages[sha] = body.strip()
    if not messages:
        return {}
    patches = git_out("log", "--first-parent", "-p", "--no-color", "--format=commit %H", rev_range, cwd=cwd)
    ids: dict[str, str] = {}
    for line in git("patch-id", "--stable", cwd=cwd, input=patches + "\n", check=False).stdout.splitlines():
        if len(words := line.split()) >= 2:
            ids[words[1]] = words[0]
    return {sha: (ids.get(sha, ""), body) for sha, body in messages.items()}


def abort_loss(cwd: Path | str | None, progress: RebaseProgress) -> AbortLoss:
    """What ``git rebase --abort`` would throw away at the stop *progress* is at.

    Four git calls for the commits (their changes against the branch's before the rebase), one
    ``git diff`` pair at a stop with the commit applied.
    """
    applied = progress.stop not in (RebaseStop.CONFLICT, RebaseStop.RESOLVED)
    uncommitted = tracked_changes(cwd).files if applied else ()
    rewritten: list[RewrittenCommit] = []
    if progress.onto and progress.orig_head:
        made = _changes(cwd, f"{progress.onto}..HEAD")
        base = git_out("merge-base", progress.onto, progress.orig_head, cwd=cwd)
        before = set(_changes(cwd, f"{base}..{progress.orig_head}").values())
        new = [sha for sha in made if made[sha] not in before and not _on_branch(cwd, sha, progress.orig_head)]
        for sha in reversed(new):  # oldest first
            short, subject = git_out("log", "-1", "--format=%h%x00%s", sha, cwd=cwd).split("\x00", 1)
            rewritten.append(RewrittenCommit(sha, short, subject))
    return AbortLoss(tuple(uncommitted), tuple(rewritten), resolution=progress.stop is RebaseStop.RESOLVED)


def _on_branch(cwd: Path | str | None, sha: str, orig_head: str) -> bool:
    return git("merge-base", "--is-ancestor", sha, orig_head, cwd=cwd, check=False).returncode == 0


def keep_uncommitted(cwd: Path | str | None, loss: AbortLoss, stopped_at: str) -> str | None:
    """Stash *loss*'s uncommitted files before the abort resets them: the stash's name, ``None`` with nothing kept.

    ``git stash create`` and ``store``: the work tree is left as it is (the abort resets it), and the
    stash says where it came from (*stopped_at*, ``33e6b1b matrix demo: …``).
    """
    if not loss.uncommitted:
        return None
    message = f"ger rebase --abort: not amended at {stopped_at}"
    created = git("stash", "create", message, cwd=cwd, check=False).stdout.strip()
    if not created:
        return None
    if git("stash", "store", "-m", message, created, cwd=cwd, check=False).returncode != 0:
        return None
    return "stash@{0}"
