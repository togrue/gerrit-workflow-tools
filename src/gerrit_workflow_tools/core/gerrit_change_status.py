"""Shared Gerrit change status for local commits (used by ``ger log`` and ``ger show``)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import quote

from gerrit_workflow_tools.core.ci_links import CiLink, CiPipeline
from gerrit_workflow_tools.core.git_run import commit_sha_or_none, git
from gerrit_workflow_tools.core.reviewer import ReviewerAccount


if TYPE_CHECKING:
    from gerrit_workflow_tools.core.anchor_mapping import AnchorPlacement
    from gerrit_workflow_tools.core.gerrit.change_view import ChangeView
    from gerrit_workflow_tools.core.stack import Commit


logger = logging.getLogger(__name__)

LocalCommentState = Literal["note", "local-draft"]
"""A comment that lives only in the local comment store."""
CommentState = Literal["note", "local-draft", "draft", "published"]


class PatchsetStatus(str, Enum):
    """Bounded Gerrit-vs-local patchset states for one commit."""

    ACTIVE = "active"
    NEWER = "newer"
    OUTDATED = "outdated"
    ABSENT = "absent"
    MERGED_SAME = "merged-same"
    MERGED_DRIFT = "merged-drift"
    MERGED_UNKNOWN = "merged-unknown"
    UNKNOWN = "unknown"
    """Gerrit could not be asked (unreachable, credentials rejected): nothing is known, not even absence."""


@dataclass(frozen=True)
class ContextLine:
    """One source line from Gerrit ``CommentInfo.context_lines``."""

    line_number: int
    text: str


@dataclass(frozen=True)
class InlineComment:
    """Normalized inline comment payload."""

    path: str
    line: int | None
    message: str
    comment_id: str | None = None
    author: str | None = None
    context_lines: tuple[ContextLine, ...] = ()
    patch_set: int | None = None
    unresolved: bool = False
    """This comment's own flag. The chain takes the flag of its last comment."""
    draft: bool = False
    """A Gerrit draft: visible only to its author until published."""
    local: LocalCommentState | None = None
    """Set for a note or local draft: it is in the local comment store, not on Gerrit."""

    @property
    def state(self) -> CommentState:
        """Where the comment is in its life: note, local draft, draft or published."""
        if self.local is not None:
            return self.local
        return "draft" if self.draft else "published"


@dataclass(frozen=True)
class CommentChain:
    """Thread of Gerrit inline comments (root + replies via ``in_reply_to``)."""

    root_id: str
    path: str
    line: int | None
    comments: tuple[InlineComment, ...]
    resolved: bool
    """Gerrit's published state: the flag of the last *published* comment."""
    start_line: int | None = None
    """First line of a range comment; ``line`` is then its last line."""
    on_parent: bool = False
    """The chain sits on the parent side of the diff (Gerrit ``side: PARENT``)."""
    anchor: AnchorPlacement | None = None
    """A local comment's anchor, and where it lands on the file version shown."""

    @property
    def anchor_moved(self) -> bool:
        """A local comment shown on a version of the file it was not written on."""
        return self.anchor is not None and self.anchor.moved

    @property
    def outdated(self) -> bool:
        """A local comment whose anchored lines changed since it was written."""
        return self.anchor is not None and self.anchor.outdated

    @property
    def local(self) -> bool:
        """A chain from the local comment store: it has no Gerrit URL."""
        return any(c.local is not None for c in self.comments)

    @property
    def answered(self) -> InlineComment:
        """The comment a reply answers: the newest published one."""
        published = [c for c in self.comments if not c.draft]
        return (published or list(self.comments))[-1]

    @property
    def draft(self) -> InlineComment | None:
        """Your unpublished reply in this chain, if you have one."""
        drafts = [c for c in self.comments if c.draft]
        return drafts[-1] if drafts else None

    @property
    def resolved_after_publish(self) -> bool:
        """The state once your drafts are published: drafts sort last, and the last comment decides."""
        return not self.comments[-1].unresolved if self.comments else self.resolved


@dataclass
class LogCommit:  # pylint: disable=too-many-instance-attributes
    """Aggregated local+Gerrit status for a commit shown by CLI status commands."""

    sha: str
    short_sha: str
    summary: str
    change_id: str | None
    pushed: bool  # True if a Gerrit change exists for this Change-Id (any patchset state)
    abandoned: bool  # True when Gerrit change status is ABANDONED
    patchset_status: PatchsetStatus
    verified: int | None  # -1, 0, +1; None = no vote
    code_review: int | None  # -2, -1, 0, +1, +2; None = no vote
    comments_unresolved: int
    ci_failures: list[str] = field(default_factory=list)
    ci_links: list[CiLink] = field(default_factory=list)
    ci_pipelines: list[CiPipeline] = field(default_factory=list)
    gerrit_url: str | None = None
    number: int | None = None  # the Gerrit change number, once pushed
    last_pushed_votes: tuple[int | None, int | None] | None = None
    """(Verified, Code-Review) on the pushed patchset while this commit is ahead of it with other code.

    Shown for orientation only: Gerrit may not copy them to the patchset a push creates. A
    commit that is only rebased (same patch-id) keeps its votes in ``verified``/``code_review``.
    """
    submittable: bool = False
    attention_reasons: list[str] = field(default_factory=list)
    change_status: str | None = None  # raw Gerrit ``status`` (e.g. NEW, MERGED)
    merged_equivalent: bool | None = None  # MERGED only: proved same / different / unknown
    reviewers: list[ReviewerAccount] = field(default_factory=list)
    updated: str | None = None
    """Gerrit ``ChangeInfo.updated`` timestamp (inbox wait-age, display). Not the follow-up
    cache key — that is :attr:`freshness`."""
    freshness: str | None = None
    """Cache validity key for comments/checks hanging off this change. ``meta_rev_id`` when
    Gerrit sends it, otherwise ``updated`` plus comment counts. Same-second comment
    resolves leave ``updated`` unchanged."""
    wip: bool = False
    private: bool = False
    diverged: bool = False
    """``newer``, and Gerrit's current patch set is a commit this repository never had: somebody
    else (or another checkout) uploaded it, so a push would put this commit over work it was not built on."""
    invalid_footer: str | None = None
    """The **footer value** when the commit has one that is not a valid **Change-Id** (``change_id``
    is then ``None``): what lets attention call the footer invalid rather than missing."""


def _is_merge_commit(cwd: Path | str | None, sha: str) -> bool:
    """True if *sha* has a second parent (merge commit)."""
    p = git("rev-parse", "--verify", f"{sha}^2", cwd=cwd, check=False)
    return p.returncode == 0


def _patch_id_single_parent(cwd: Path | str | None, sha: str) -> str | None:
    """Return ``git patch-id`` first token for the single-commit diff against its first parent."""
    p = git("rev-parse", "--verify", f"{sha}^", cwd=cwd, check=False)
    if p.returncode != 0:
        return None
    parent = p.stdout.strip()
    diff_p = git("diff", f"{parent}..{sha}", cwd=cwd, check=False)
    if diff_p.returncode != 0 or not diff_p.stdout.strip():
        return None
    pid = git("patch-id", cwd=cwd, input=diff_p.stdout, check=False)
    if pid.returncode != 0 or not pid.stdout.strip():
        return None
    line = pid.stdout.strip().splitlines()[0]
    parts = line.split()
    return parts[0] if parts else None


def compute_merged_equivalent(
    local_sha: str,
    change: ChangeView,
    cwd: Path | str | None,
) -> bool | None:
    """
    v1 conservative equivalence: SHA match, else patch-id match when both sides are single-parent
    and the Gerrit ``current_revision`` exists locally. Merge commits and missing objects → ``None``.
    """
    cur = change.current_revision
    if cur is None:
        return None
    if norm_sha(local_sha) == norm_sha(cur):
        return True
    if commit_sha_or_none(cwd, cur) is None:
        return None
    if _is_merge_commit(cwd, local_sha) or _is_merge_commit(cwd, cur):
        return None
    pl = _patch_id_single_parent(cwd, local_sha)
    pg = _patch_id_single_parent(cwd, cur)
    if pl is None or pg is None:
        return None
    return pl == pg


def norm_sha(sha: str) -> str:
    """Normalize commit SHA text for case-insensitive comparisons."""

    return sha.strip().lower()


def patchset_status(local_sha: str, change: ChangeView) -> PatchsetStatus:
    """Compare local commit SHA to Gerrit ``current_revision`` / ``revisions``."""
    ls = norm_sha(local_sha)
    cur_n = change.current_revision
    rev_keys = set(change.revisions)
    if cur_n is None and len(rev_keys) == 1:
        cur_n = next(iter(rev_keys))
    if cur_n and ls == cur_n:
        return PatchsetStatus.ACTIVE
    if rev_keys and ls in rev_keys and cur_n and ls != cur_n:
        return PatchsetStatus.OUTDATED
    if cur_n or rev_keys:
        return PatchsetStatus.NEWER
    return PatchsetStatus.ABSENT


def gerrit_inline_comment_url(change_page_url: str | None, comment_id: str | None) -> str | None:
    """Build a PolyGerrit URL that opens the diff view focused on one inline comment.

    Matches the ``/c/<project>/+/<changeNum>/comment/<commentId>/`` route (see Gerrit
    ``gr-router`` COMMENT pattern). *comment_id* is the REST ``CommentInfo.id`` (URL-encoded
    UUID string).
    """
    if not change_page_url or not comment_id or not str(comment_id).strip():
        return None
    base = change_page_url.rstrip("/")
    cid_enc = quote(str(comment_id), safe="")
    return f"{base}/comment/{cid_enc}/"


def local_log_commit(row: Commit, status: PatchsetStatus) -> LogCommit:
    """A ``LogCommit`` carrying no Gerrit state: *status* says whether that is absence or ignorance.

    A commit without a valid Change-Id is ``ABSENT`` either way: Gerrit cannot hold it.
    """
    footer = (row.change_id or "").strip()
    return LogCommit(
        sha=row.sha,
        short_sha=row.short_sha,
        summary=row.subject,
        change_id=row.valid_change_id,
        pushed=False,
        abandoned=False,
        patchset_status=status if row.valid_change_id else PatchsetStatus.ABSENT,
        verified=None,
        code_review=None,
        comments_unresolved=0,
        invalid_footer=footer if footer and not row.valid_change_id else None,
    )


def build_log_commit(
    row: Commit,
    change: ChangeView | None,
    web_base: str,
    cwd: Path | str | None,
) -> tuple[LogCommit, frozenset[str]]:
    """Build one ``LogCommit`` from the Gerrit change matching *row*.

    Returns ``(commit, follow_up_kinds)`` where *follow_up_kinds* names REST
    calls the caller should make to fill absent data: ``'comments'``,
    ``'checks'``, ``'reviewers'``.  An empty set means the commit is fully
    populated from *change* alone.
    """
    from gerrit_workflow_tools.core.attention import ci_failed

    sha, short, summary, change_id = row.sha, row.short_sha, row.subject, row.valid_change_id

    if not change_id or change is None:
        return local_log_commit(row, PatchsetStatus.ABSENT), frozenset()

    st = change.status.upper() if change.status else ""
    needed: set[str] = set()
    if change.unresolved_comments is None:
        needed.add("comments")
    if ci_failed(change.verified):
        needed.add("checks")
    # An empty list is an *answer* when the payload carried the field: `DETAILED_LABELS`
    # puts `reviewers` on every ChangeInfo, so absence — not emptiness — is the miss.
    if change.reviewers is None:
        needed.add("reviewers")

    merged_eq: bool | None = None
    if st == "MERGED":
        merged_eq = compute_merged_equivalent(sha, change, Path.cwd() if cwd is None else Path(cwd))
        if merged_eq is True:
            ps = PatchsetStatus.MERGED_SAME
        elif merged_eq is False:
            ps = PatchsetStatus.MERGED_DRIFT
        else:
            ps = PatchsetStatus.MERGED_UNKNOWN
    else:
        ps = patchset_status(sha, change)

    # Votes and submittability belong to the patch set on Gerrit, not to a commit it has not seen.
    newer = ps == PatchsetStatus.NEWER
    # Unless the commit's own code is the same (only rebased): Gerrit carries the votes over
    # on such a push, so they stay. Two git calls, and only for commits ahead of Gerrit.
    same_code = newer and compute_merged_equivalent(sha, change, Path.cwd() if cwd is None else Path(cwd)) is True
    votes_moved_on = newer and not same_code
    current = change.current_revision
    diverged = newer and current is not None and commit_sha_or_none(cwd, current) is None
    if votes_moved_on:
        needed.discard("checks")

    return (
        LogCommit(
            sha=sha,
            short_sha=short,
            summary=summary,
            change_id=change_id,
            pushed=True,
            abandoned=st == "ABANDONED",
            patchset_status=ps,
            verified=None if votes_moved_on else change.verified,
            code_review=None if votes_moved_on else change.code_review,
            comments_unresolved=change.unresolved_comments or 0,
            gerrit_url=change.url(web_base),
            number=change.number,
            last_pushed_votes=(change.verified, change.code_review) if votes_moved_on else None,
            submittable=change.submittable and not newer,
            change_status=change.status,
            merged_equivalent=merged_eq,
            reviewers=list(change.reviewers or ()),
            updated=change.updated,
            freshness=change.freshness,
            wip=change.work_in_progress,
            private=change.private,
            diverged=diverged,
        ),
        frozenset(needed),
    )
