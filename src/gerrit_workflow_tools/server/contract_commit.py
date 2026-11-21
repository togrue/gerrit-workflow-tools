"""The ``ger serve`` contract for one commit: its overlay, comments, CI findings and diff.

Part of the contract :mod:`~gerrit_workflow_tools.server.contract` gathers.
"""

from __future__ import annotations

from typing import Literal

from typing_extensions import NotRequired, TypedDict

from gerrit_workflow_tools.core.attention import Severity
from gerrit_workflow_tools.core.changeish import ChangeishKind
from gerrit_workflow_tools.core.ci_content import CacheVerdict
from gerrit_workflow_tools.core.ci_links import CiLinkSource
from gerrit_workflow_tools.core.code_facts import CodeChange
from gerrit_workflow_tools.core.gerrit.resolution import SelectedReason
from gerrit_workflow_tools.core.gerrit_change_status import CommentState, PatchsetStatus


# --- The overlay of one commit --------------------------------------------------------------


class CiLink(TypedDict):
    label: str
    url: str
    build_url: str
    """The build's URL as CI reported it, where ``url`` was derived from it."""
    source: CiLinkSource


class CiPipeline(TypedDict):
    label: str
    state: str
    url: NotRequired[str]


class Vote(TypedDict):
    label: str
    value: int
    by: str


class Reviewer(TypedDict):
    """Someone Gerrit lists on a change: a reviewer, or (``cc``) only copied."""

    name: str
    cc: bool


class Votes(TypedDict):
    verified: int | None
    code_review: int | None


class Overlay(TypedDict):
    """The overlay state of one commit, but its ``sha``: what ``ger log --json`` and ``ger show --json`` share."""

    short_sha: str
    change_id: str | None
    summary: str
    pushed: bool
    patchset_status: PatchsetStatus
    verified: int | None
    code_review: int | None
    comments_unresolved: int
    ci_failures: list[str]
    ci_links: list[CiLink]
    ci_pipelines: list[CiPipeline]
    gerrit_url: str | None
    number: int | None
    """The Gerrit change number; null before the first push."""
    last_pushed_votes: Votes | None
    """The pushed patchset's votes while this commit is ahead of it with other code (its own votes are then null)."""
    code_change: CodeChange | None
    """``newer`` only, else null: whether its code changed since the pushed patchset (``changed``), or it is only
    rebased and keeps its votes (``rebased``); ``unknown`` when core cannot tell."""
    submittable: bool
    votes: list[Vote]
    reviewers: list[Reviewer]
    work_in_progress: bool
    private: bool
    attention_reasons: list[str]
    severity: Severity
    change_status: str | None
    """Gerrit's own status of the change: ``NEW``, ``MERGED``, ``ABANDONED``."""
    merged_equivalent: bool | None
    updated: str | None


class CommitStatus(Overlay):
    """The overlay state of one commit."""

    sha: str | None


# --- Comments ---------------------------------------------------------------------------------


class CommentRange(TypedDict):
    start_line: int
    start_character: int
    end_line: int
    end_character: int


class FixReplacement(TypedDict):
    """A suggested edit's replacement: Gerrit's FixReplacementInfo, and ``location`` as ``ger comment`` writes it."""

    path: str
    range: CommentRange
    replacement: str
    location: str


class SuggestedEdit(TypedDict):
    """An edit a comment suggests: a Gerrit fix suggestion, or a suggestion block of its text (``in_message``)."""

    fix_id: str | None
    description: str
    in_message: bool
    replacements: list[FixReplacement]


class ContextLine(TypedDict):
    line_number: int
    context_line: str


class Comment(TypedDict):
    path: str
    line: int | None
    message: str
    url: str | None
    patch_set: int | None
    draft: bool
    state: CommentState
    author: NotRequired[str]
    comment_id: NotRequired[str]
    context_lines: NotRequired[list[ContextLine]]
    suggested_edits: NotRequired[list[SuggestedEdit]]
    text: str
    """``message`` with its fix suggestions written out below it, as ``ger show`` prints them."""
    markdown: str
    """The same in Markdown, each replacement fenced: for an editor's comment threads."""


class CommentChain(TypedDict):
    root_id: str
    path: str
    line: int | None
    start_line: int | None
    url: str | None
    resolved: bool
    anchor_moved: bool
    outdated: bool
    anchored_path: str | None
    """Where a local comment was written; null for a Gerrit chain."""
    anchored_start_line: int | None
    anchored_line: int | None
    anchored_text: str | None
    """The text a local comment covered when it was written."""
    resolved_after_publish: bool
    held: bool
    """On hold: you keep this chain for yourself, the agent runbook leaves it alone."""
    comments: list[Comment]


NewCommentState = Literal["draft", "local-draft", "note"]
"""What a new comment becomes: a draft on Gerrit, or kept here (a local draft or a private note)."""


# --- CI ----------------------------------------------------------------------------------------

FindingSeverity = Literal["error", "warning"]


class CiFinding(TypedDict):
    tool: str
    kind: str
    path: str | None
    line: int | None
    column: int | None
    message: str
    detail: str | None
    fingerprint: str
    severity: FindingSeverity
    """A warning ranks below an error: it is rarely why the build failed."""


class CiProvenance(TypedDict):
    url: str
    provider: str | None
    build: str | None
    fetched_at: str | None
    truncated: bool
    cache_verdict: CacheVerdict
    cached: bool
    error: str | None


class CiContent(TypedDict):
    """One CI link's findings, and where they came from."""

    label: str
    url: str
    build_url: str
    source: CiLinkSource
    provenance: CiProvenance
    findings: list[CiFinding]
    collapse_warnings: bool
    """Some error explains the failure, so the warnings step back behind a count."""


# --- Show and diff ----------------------------------------------------------------------------


class SelectedChange(TypedDict):
    number: int
    triplet: str
    branch: str
    change_id: str
    status: str


class LocalSibling(TypedDict):
    sha: str
    branch: str
    number: int | None


class Resolution(TypedDict):
    """What a changeish resolved to."""

    input: str
    kind: ChangeishKind
    ambiguous: bool
    alternatives: list[SelectedChange]
    selected: NotRequired[SelectedChange]
    selected_reason: NotRequired[SelectedReason]
    local_sha: NotRequired[str]
    local_sibling: NotRequired[LocalSibling]


class FileChange(TypedDict):
    path: str
    status: str
    """Git's status letter: ``A``, ``M``, ``D``, ``R`` (renamed), ``C`` (copied), ``T`` (type changed)."""
    old_path: str | None
    insertions: int | None
    """Null for a binary file."""
    deletions: int | None


class DeltaSince(TypedDict):
    """A review delta: patchset ``patch_set`` against ``to_patch_set`` (null: a local commit)."""

    patch_set: int | None
    current_patch_set: int
    to_patch_set: int | None
    up_to_date: bool
    identical: bool
    rebased: bool


class Diff(TypedDict):
    """The files a commit touched, or a review delta."""

    commit: str
    base: str | None
    """Null only in a review delta with nothing to compare against."""
    subject: NotRequired[str | None]
    message: NotRequired[str]
    """The whole commit message; not in a review delta."""
    author: NotRequired[str]
    """``Name <email>``; not in a review delta."""
    local_commit: bool
    files: list[FileChange]
    since: NotRequired[DeltaSince]
    """Present in a review delta."""


class ShownCommit(Overlay):
    """One commit as ``ger show --json`` has it, with what a viewer asks for beside it."""

    sha: str | None
    comments: list[Comment]
    comment_chains: list[CommentChain]
    local_commit: bool
    resolution: Resolution
    new_comment: Literal["draft", "local-draft"]
    """What a new comment on it becomes by default: a draft where Gerrit has this commit, else a local draft."""
    ci_content: NotRequired[list[CiContent]]
    ci_introduced: NotRequired[list[str] | None]
    """Fingerprints of the findings this change introduced; null when that cannot be told."""
    markdown: NotRequired[str]
    """With ``markdown``: the commit as ``ger show --ai`` prints it, for an agent."""
    files: NotRequired[Diff]
    """With ``files``: what ``/diff`` answers for the commit."""


class Shown(TypedDict):
    commits: list[ShownCommit]


class PatchsetEntry(TypedDict):
    number: int
    commit: str
    created: str | None


class Patchsets(TypedDict):
    """The patchsets Gerrit has of one change, oldest first."""

    number: int
    current: int | None
    reviewed: int | None
    """The newest patchset you posted on."""
    patchsets: list[PatchsetEntry]


class FileText(TypedDict):
    rev: str
    path: str
    content: str | None
    """Null when the file is not there at ``rev``."""
