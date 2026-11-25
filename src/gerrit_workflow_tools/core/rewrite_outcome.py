"""What a finished rewrite of the **local stack** did, as data: the commits it rewrote, squashed and dropped.

Told from the stack before (taken when the rewrite started, or the stack at a rebase's
``orig-head``) and the stack now, so it costs a ``git log`` and no request: whether a commit's
change is on Gerrit comes from what the cache last saw of it. Saying it is the command's.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from gerrit_workflow_tools.core.config import ConfigError
from gerrit_workflow_tools.core.gerrit.change_view import ChangeView
from gerrit_workflow_tools.core.gerrit.stack_context import ChangeResolutionError
from gerrit_workflow_tools.core.git_run import git_out
from gerrit_workflow_tools.core.run_context import RunContext
from gerrit_workflow_tools.core.stack import (
    Commit,
    commits_in_range,
    fixed_subject,
    get_stack_snapshot,
    is_unsquashed_fixup,
    read_commit_rows,
)


@dataclass(frozen=True)
class DroppedCommit:
    """A commit of the stack before that the stack now lacks, and no squash or merge upstream explains: dropped."""

    sha: str
    short_sha: str
    subject: str
    change_number: int | None = None
    """Its change, open on Gerrit as the cache last saw it: leaving the commit out does not abandon it."""


@dataclass(frozen=True)
class RewriteOutcome:
    """The local stack after a rewrite, against the stack before it."""

    commits: int
    """Commits in the local stack now."""
    rewritten: int
    """Of them, the ones with a new id: what ``ger push`` would upload."""
    on_gerrit: int | None
    """Of the rewritten ones, those whose change is open on Gerrit: now ahead of it.

    ``None`` without a Gerrit configured, whose cache could say."""
    dropped: tuple[DroppedCommit, ...] = ()
    squashed: int = 0
    """``fixup!``/``squash!`` commits of the stack before that are gone: folded into their commits."""
    upstream: int = 0
    """Commits left out because their change is now in the upstream (merged, dropped as equivalent)."""
    fixups_left: int = 0
    """``fixup!``/``squash!`` commits still in the stack: to squash before pushing."""


def stack_before(run: RunContext, head: str = "HEAD") -> tuple[Commit, ...]:
    """The local stack as it is at *head*: what :func:`rewrite_outcome` compares the stack after with."""
    return get_stack_snapshot(run.cwd, run.repo.working_branch, head=head, settings=run.settings).commits


def _same(a: Commit, b: Commit) -> bool:
    """One change: the same Change-Id, else (no Change-Id on either) the same subject."""
    if a.valid_change_id or b.valid_change_id:
        return (a.valid_change_id or "").lower() == (b.valid_change_id or "").lower()
    return a.subject == b.subject


def _open_changes(run: RunContext, change_ids: Sequence[str]) -> dict[str, int] | None:
    """Change number by Change-Id, for those the cache has open on the push branch; ``None`` without a Gerrit.

    Local only: whether a change exists on Gerrit does not go stale the way its votes do.
    """
    if not change_ids:
        return {}
    if not run.gerrit.configured:
        return None
    try:
        stack = run.stack
        cached = run.gerrit.changes.find_by_footer_change_ids(list(change_ids))
    except (ConfigError, ChangeResolutionError):
        return None
    found: dict[str, int] = {}
    for change_id, payloads in cached.items():
        for view in map(ChangeView.from_payload, payloads):
            on_branch = view.project == stack.project and view.branch == stack.push_branch
            if on_branch and view.status == "NEW" and view.number is not None:
                found[change_id.lower()] = view.number
    return found


def rewrite_outcome(run: RunContext, before: Sequence[Commit]) -> RewriteOutcome:
    """What the rewrite that turned the local stack *before* into the stack now did."""
    after = stack_before(run)
    was = {c.sha for c in before}
    rewritten = [c for c in after if c.sha not in was]
    gone = [c for c in before if not any(_same(c, now) for now in after)]
    # A fixup is squashed when the commit it fixes was rewritten; one dropped on its own is dropped.
    squashed = [
        c for c in gone if is_unsquashed_fixup(c.subject) and fixed_subject(c.subject) in {r.subject for r in rewritten}
    ]
    gone = [c for c in gone if c not in squashed]
    upstream: list[Commit] = []
    if gone and before:
        # Rebased onto an upstream that has them: their change is merged, nothing was lost.
        fork = git_out("merge-base", before[-1].sha, "HEAD", cwd=run.cwd)
        below = [c for c in commits_in_range(run.cwd, f"{fork}..HEAD") if c.sha not in {a.sha for a in after}]
        upstream = [c for c in gone if any(_same(c, there) for there in below)]
        gone = [c for c in gone if c not in upstream]
    ids = [c.valid_change_id for c in (*rewritten, *gone) if c.valid_change_id]
    open_changes = _open_changes(run, ids)
    known = open_changes or {}
    return RewriteOutcome(
        commits=len(after),
        rewritten=len(rewritten),
        on_gerrit=None
        if open_changes is None
        else sum(1 for c in rewritten if c.valid_change_id and c.valid_change_id.lower() in known),
        dropped=tuple(
            DroppedCommit(
                c.sha,
                c.short_sha,
                c.subject,
                known.get(c.valid_change_id.lower()) if c.valid_change_id else None,
            )
            for c in gone
        ),
        squashed=len(squashed),
        upstream=len(upstream),
        fixups_left=sum(1 for c in after if is_unsquashed_fixup(c.subject)),
    )


def dropped_commit(run: RunContext, sha: str) -> DroppedCommit:
    """The commit *sha* as :class:`DroppedCommit`: the one a ``ger rebase --skip`` left out."""
    commit = read_commit_rows(run.cwd, "-1", sha)[0]
    known = _open_changes(run, [commit.valid_change_id] if commit.valid_change_id else []) or {}
    number = known.get(commit.valid_change_id.lower()) if commit.valid_change_id else None
    return DroppedCommit(commit.sha, commit.short_sha, commit.subject, number)
