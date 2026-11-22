"""The ``ger serve`` contract for the local stack and the inbox: review chains, the headline, the overview.

Part of the contract :mod:`~gerrit_workflow_tools.server.contract` gathers.
"""

from __future__ import annotations

from typing import Literal

from typing_extensions import NotRequired, TypedDict

from gerrit_workflow_tools.core.attention import Severity
from gerrit_workflow_tools.core.push_checks import PushBlocker
from gerrit_workflow_tools.core.rebase_state import RebaseAction, RebaseStop
from gerrit_workflow_tools.core.rebasing_stack import RebaseStep
from gerrit_workflow_tools.core.reorder import StepAction
from gerrit_workflow_tools.core.reorder_preview import PickStop
from gerrit_workflow_tools.server.contract_commit import Overlay


# --- Errors -----------------------------------------------------------------------------------


class ApiError(TypedDict):
    """A failure: the shared exit-code name, and why."""

    code: str
    message: str
    reason: NotRequired[str]
    """A word for a refusal the client can do something about (``uncommitted-changes``)."""


# --- Review chains and the inbox --------------------------------------------------------------


class Member(TypedDict):
    """One change of a review chain."""

    number: int
    change_id: str
    subject: str
    verified: int | None
    code_review: int | None
    comments_unresolved: int
    attention_reasons: list[str]
    severity: Severity
    url: str | None
    sha: str | None
    """The commit of the current patch set on Gerrit."""
    local_sha: NotRequired[str | None]
    """In an overview: the local stack commit carrying this Change-Id, if there is one."""


class ChainTop(TypedDict):
    number: int
    change_id: str
    subject: str
    url: str | None


class Owner(TypedDict):
    name: str
    email: str | None


class Chain(TypedDict):
    """A review chain: its top, clocks, worst votes, and members base to top."""

    key: str
    top: ChainTop
    project: str
    branch: str
    owner: Owner
    depth: int
    wait_age_seconds: int
    unreviewed_age_seconds: int
    last_activity: str | None
    verified: int | None
    code_review: int | None
    comments_unresolved: int
    attention_reasons: list[str]
    severity: Severity
    partial_chain: bool
    members: list[Member]
    local: NotRequired[bool]
    """In an overview: some member is a commit of the local stack."""


SectionName = Literal["to-review", "outgoing"]


class SectionSummary(TypedDict):
    """Counts over one section's chains."""

    chains: int
    changes: int
    oldest_unreviewed_seconds: int
    oldest_wait_seconds: int
    ci_failures: int
    """Chains with failed CI."""
    comments: int
    """Chains with unresolved comments."""


class Section(TypedDict):
    name: SectionName
    query: str
    summary: SectionSummary
    chains: list[Chain]


class ChainHeadline(TypedDict):
    """A change of a review chain in an inbox section: the one to look at first, and a sentence saying why."""

    section: SectionName
    reason: str
    severity: Severity
    """The reason's severity; ``pending`` for one waiting on your review."""
    chain: str
    number: int
    subject: str
    url: str | None
    text: str


class RebaseHeadline(TypedDict):
    """A rebase underway in the checkout: while there is one, core makes it the headline."""

    section: Literal["local"]
    reason: Literal["rebase"]
    severity: Severity
    """``error`` for a conflict, else ``warning``."""
    stop: RebaseStop | None
    """As ``rebase.stop`` of ``GET /stack``."""
    chain: None
    number: int | None
    """The commit it stopped at: its change number; null when it is not on Gerrit or there is no such commit."""
    subject: str | None
    sha: str | None
    url: str | None
    text: str


Headline = ChainHeadline | RebaseHeadline
"""The headline: the one thing that most wants you, as core picks it."""


# --- The local stack ------------------------------------------------------------------------


class StackCommit(Overlay):
    """One commit of the local stack, as ``ger log`` shows it."""

    sha: str
    abandoned: bool
    is_head: bool
    held_back: bool
    """Above the ready boundary: ``ger push`` leaves it out."""
    needs_edit: bool
    diverged: bool
    """``newer``, and Gerrit's current patchset is a commit this repository never had: it came from elsewhere."""
    merge: bool
    """A merge commit: one stack commit, whatever it brings in."""
    pinned: bool
    """The newest merge or below it: git rebase cannot replay a merge, so core leaves it where it is."""
    rebase: RebaseStep | None
    """Where a rebase underway stands on this commit; null without one, or when it does not replay it."""
    resolution_note: NotRequired[str]


# For each way on a rebase's ``actions`` leave out, the refusal ``ger rebase --<action>`` prints.
# The functional form: ``continue`` is a Python keyword, so it cannot name a field of a class.
RebaseRefusals = TypedDict(
    "RebaseRefusals", {"continue": NotRequired[str], "abort": NotRequired[str], "skip": NotRequired[str]}
)


class Rebase(TypedDict):
    """A rebase underway, as ``GET /stack`` and ``ger log --json`` serve it."""

    branch: str | None
    """The branch being rebased; null for a detached HEAD's."""
    onto: str | None
    orig_head: str | None
    stop: RebaseStop | None
    """Why it is stopped; null when core cannot tell what ``git rebase --continue`` would do from here."""
    applied: bool
    """False while the commit it stopped at is only partly there (a conflict, resolved or not)."""
    current: str | None
    """The commit it stopped at, as the stack shows it."""
    unmerged: list[str]
    """Paths the index still holds as unresolved conflicts."""
    as_before: bool
    """The stack is the branch as it was before the rebase (git's ``apply`` backend keeps no todo).

    No commit then has a step."""
    actions: list[RebaseAction]
    """The ways on core allows from this stop, in order: the only ones to offer."""
    refused: RebaseRefusals
    """What to say when one of the other ways on is asked for anyway."""


class PushBlocked(TypedDict):
    """What is underway that no Gerrit push may run over."""

    reason: PushBlocker
    message: str
    """The refusal ``ger push`` prints."""


class StackTarget(TypedDict):
    """The branches are null without a working branch (a detached HEAD's rebase)."""

    project: str
    target_branch: str | None
    push_branch: str | None


class StackSummary(TypedDict):
    commits: int
    ready: int
    attention: int
    needs_edit: int
    not_on_gerrit: int


class Stack(TypedDict):
    """The annotated stack of the working branch, with the ready boundary: ``ger log`` as JSON."""

    branch: str | None
    """The branch checked out: null while a rebase runs (see ``rebase.branch``)."""
    head: str | None
    rebase_in_progress: bool
    rebase: Rebase | None
    """The rebase underway; the stack is then as the rebase has it so far, the rest as it was."""
    push_blocked: PushBlocked | None
    """Why ``ger push`` would refuse now, whatever the stack holds; null when it would not."""
    upstream: UpstreamMoved | None
    """How far the upstream moved on past the stack, and the rebase onto it previewed; null without one, and during
    a rebase."""
    stack: StackTarget
    summary: StackSummary
    commits: list[StackCommit]
    gerrit_unavailable: NotRequired[ApiError]
    """Gerrit could not be asked: the stack is shown with what git knows."""


class LocalError(TypedDict):
    """The local stack could not be shown."""

    error: ApiError
    needs_upstream: bool
    """Because the branch has no upstream: ``POST /stack/upstream`` gives it one."""


class Overview(TypedDict):
    """Both inbox sections, the headline, and the local stack beside them.

    The inbox and the local stack fail separately: either may be an error while the other is shown.
    """

    host: str
    generated: NotRequired[str]
    headline: NotRequired[Headline | None]
    projects: NotRequired[list[str]]
    """The Gerrit projects the sections are narrowed to: this repository's. Empty means all."""
    sections: NotRequired[list[Section]]
    error: NotRequired[ApiError]
    """Set instead of ``sections`` when the inbox could not be loaded."""
    local: Stack | LocalError


class MoveConflict(TypedDict):
    """Why two neighbours of the local stack cannot swap places."""

    sha: str
    summary: str
    files: list[str]


class UpstreamStop(MoveConflict):
    """Where the rebase onto the upstream would stop: a conflict in ``files``, or a commit that would become empty
    (its change is upstream already)."""

    kind: PickStop


class DroppedCommit(TypedDict):
    sha: str
    summary: str


class UpstreamMoved(TypedDict):
    """The commits the upstream has that the branch has not, and what the rebase onto it would meet."""

    name: str
    """``origin/main``, as the branch config names it."""
    behind: int
    argv: list[str]
    """The one ger command that rebases onto it, ``ger`` left out (``["rebase", "origin/main"]``, maybe
    ``--drop-merged-equivalent``): run as it is."""
    conflict: UpstreamStop | None
    """The first commit the rebase would stop on; null when all would apply (or ``preview`` is false)."""
    preview: bool
    """False when the rebase could not be previewed: nothing is known, and ``reason`` says why."""
    reason: str | None
    """Why there is no preview, in core's words; null when there is one."""
    dropped: list[DroppedCommit]
    """The commits the rebase leaves out, and so does the preview: already upstream, or merged on Gerrit with the
    same code."""


class Swap(TypedDict):
    lower: str
    upper: str
    conflict: MoveConflict | None


class StackMoves(TypedDict):
    """The conflict preview: one entry per adjacent pair of the stack, oldest first."""

    head: str | None
    available: bool
    """False when this git cannot simulate the moves: nothing is known."""
    swaps: list[Swap]
    reason: NotRequired[str]
    """Why the moves cannot be simulated."""


class UpstreamChoice(TypedDict):
    """The working branch's upstream, and which to offer: the best guess first."""

    branch: str | None
    upstream: str | None
    suggested: str | None
    why: str | None
    candidates: list[str]


class UpstreamSet(TypedDict):
    branch: str
    upstream: str


class StackStepAction(TypedDict):
    sha: str
    action: NotRequired[StepAction]
    """``pick`` when absent."""


StackStep = str | StackStepAction
"""A commit id (kept as it is), or what to do with it: fold into the commit below, or drop."""


class Rewritten(Stack):
    """The stack after a rewrite, and what ``/stack/undo`` takes back."""

    previous_head: str
    stash_kept: NotRequired[bool]
    """The stashed uncommitted changes did not go back on cleanly and stay in the stash."""
