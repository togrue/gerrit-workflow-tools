"""The ``ger serve`` contract: every query, body and answer of the API, as types.

The one source of the wire shapes, in this module and the ones of :data:`MODULES`.
``scripts/generate_serve_contract.py`` writes the TypeScript types of ``vscode/src/contract.ts``
and the JSON Schema ``docu/spec/serve-contract.schema.json`` from them, so a client never
mirrors them by hand; the tests hold every answer the endpoints give against them. Spec:
docu/spec/commands/serve.md.

A string after a field is its documentation and goes into the generated files. A field in
``NotRequired`` may be absent; ``| None`` means it is there and may be ``null``. A module-level
``Literal`` alias, or an enum of core, becomes a named type of its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType
from typing import Literal

from typing_extensions import TypedDict

from gerrit_workflow_tools.core.gerrit.event_stream import StreamState
from gerrit_workflow_tools.core.review_checkout import ReviewMode
from gerrit_workflow_tools.server import contract_commit, contract_requests, contract_stack
from gerrit_workflow_tools.server.contract_commit import CommentRange, Diff, FileText, NewCommentState, Patchsets, Shown
from gerrit_workflow_tools.server.contract_requests import (
    ActivityQuery,
    DiffQuery,
    DiscardCommentBody,
    EditCommentBody,
    FileQuery,
    HoldBody,
    NewCommentBody,
    NoBody,
    OverviewQuery,
    Query,
    ReplyBody,
    ReviewOpenBody,
    RevQuery,
    RewordBody,
    RewriteBody,
    RunbookQuery,
    ShowQuery,
    StackQuery,
    UndoBody,
    UpstreamBody,
)
from gerrit_workflow_tools.server.contract_stack import (
    ApiError,
    Chain,
    Overview,
    Rewritten,
    Stack,
    StackMoves,
    UpstreamChoice,
    UpstreamSet,
)


# --- Start-up and errors ---------------------------------------------------------------------


class Hello(TypedDict):
    """The one JSON line ``ger serve`` prints on stdout once it listens."""

    url: str
    api: str
    """The API's path prefix: ``/api/v1``."""
    token: str
    """The bearer token every request carries."""
    repo: str
    pid: int


class ErrorAnswer(TypedDict):
    """What a failed request answers, with an HTTP status to match."""

    error: ApiError


# --- Activity ------------------------------------------------------------------------------


class EventsStatus(TypedDict):
    """Where Gerrit's event stream is, as ``ger serve`` follows it."""

    state: StreamState
    reason: str | None
    """Why it is not live; null while it is."""
    source: str | None
    """``ssh://user@host:port``, or null when there is nothing to connect to."""


class Activity(TypedDict):
    """The activity sequence: it goes up whenever a view should look again."""

    activity: int
    events: EventsStatus


class Health(TypedDict):
    """Who is answering, for which repository, and whether the event stream is followed."""

    name: str
    version: str
    api: int
    repo: str | None
    branch: str | None
    host: str
    activity: int
    events: EventsStatus


# --- Review checkout ------------------------------------------------------------------------


class ReviewState(TypedDict):
    """The review state: the reviewed patchset beside where and how it is checked out."""

    number: int
    key: str
    change_id: str
    patch_set: int
    commit: str
    subject: str
    target_branch: str
    private: bool
    staged: bool
    mode: ReviewMode
    worktree: str
    created_branches: dict[str, str]
    return_to: str | None
    branch: str
    chain: list[int] | None
    url: str | None


class ReviewAnswer(TypedDict):
    review: ReviewState | None


class ReviewOpened(TypedDict):
    review: ReviewState
    moved_from: str | None


class ReviewDone(TypedDict):
    done: int
    deleted: list[str]
    kept: list[str]


# --- Comment actions --------------------------------------------------------------------------


class DraftReply(TypedDict):
    action: Literal["created", "updated"]
    draft_id: str | None
    patch_set: int | None
    unresolved: bool
    replaced_message: str | None
    """Text of the draft this one replaced; null when it was created."""


class Replied(TypedDict):
    change: str
    root_id: str
    reply: DraftReply


class CommentWritten(TypedDict):
    """A comment chain just started."""

    action: Literal["created"]
    change: str
    commit: str
    path: str
    line: int | None
    range: CommentRange | None
    state: NewCommentState
    id: str | None
    patch_set: int | None
    unresolved: bool
    url: str | None


class CommentChanged(TypedDict):
    """One of your unpublished comments edited or discarded; ``message`` is what it said before."""

    action: Literal["updated", "discarded"]
    state: NewCommentState
    id: str
    change: str
    message: str


class Holds(TypedDict):
    root_id: str
    held: bool
    holds: list[str]


# --- Agent runbook ----------------------------------------------------------------------------


class RunbookCommit(TypedDict):
    sha: str
    short_sha: str
    change_id: str | None
    summary: str
    comments: int
    held: int
    findings: int
    inherited: int
    ci_unread: int
    agent_work: bool


class RunbookTotals(TypedDict):
    commits: int
    comments: int
    held: int
    findings: int


class Runbook(TypedDict):
    """The attention walk as work for an agent: Markdown to follow, and what is in it."""

    branch: str | None
    commits: list[RunbookCommit]
    totals: RunbookTotals
    markdown: str


# --- Endpoints --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Endpoint:
    """One call: ``{name}`` in ``path`` is a path segment."""

    method: Literal["GET", "POST"]
    path: str
    query: type
    body: type
    answer: type

    @property
    def key(self) -> str:
        return f"{self.method} {self.path}"


ENDPOINTS: tuple[Endpoint, ...] = (
    Endpoint("GET", "/health", Query, NoBody, Health),
    Endpoint("GET", "/activity", ActivityQuery, NoBody, Activity),
    Endpoint("GET", "/overview", OverviewQuery, NoBody, Overview),
    Endpoint("GET", "/stack", StackQuery, NoBody, Stack),
    Endpoint("GET", "/stack/moves", Query, NoBody, StackMoves),
    Endpoint("GET", "/stack/upstream", Query, NoBody, UpstreamChoice),
    Endpoint("GET", "/chains/{number}", Query, NoBody, Chain),
    Endpoint("GET", "/show", ShowQuery, NoBody, Shown),
    Endpoint("GET", "/diff", DiffQuery, NoBody, Diff),
    Endpoint("GET", "/patchsets", RevQuery, NoBody, Patchsets),
    Endpoint("GET", "/file", FileQuery, NoBody, FileText),
    Endpoint("GET", "/review", Query, NoBody, ReviewAnswer),
    Endpoint("GET", "/agent/runbook", RunbookQuery, NoBody, Runbook),
    Endpoint("POST", "/review/open", Query, ReviewOpenBody, ReviewOpened),
    Endpoint("POST", "/review/done", Query, NoBody, ReviewDone),
    Endpoint("POST", "/comments/reply", Query, ReplyBody, Replied),
    Endpoint("POST", "/comments/new", Query, NewCommentBody, CommentWritten),
    Endpoint("POST", "/comments/edit", Query, EditCommentBody, CommentChanged),
    Endpoint("POST", "/comments/discard", Query, DiscardCommentBody, CommentChanged),
    Endpoint("POST", "/comments/hold", Query, HoldBody, Holds),
    Endpoint("POST", "/stack/upstream", Query, UpstreamBody, UpstreamSet),
    Endpoint("POST", "/stack/rewrite", Query, RewriteBody, Rewritten),
    Endpoint("POST", "/stack/reword", Query, RewordBody, Rewritten),
    Endpoint("POST", "/stack/undo", Query, UndoBody, Stack),
)
"""Every call of the API, under its prefix ``/api/v<API_VERSION>``."""

MODULES: tuple[ModuleType, ...] = (contract_commit, contract_stack, contract_requests)
"""Where the rest of the contract lives; with this module, everything the generated files hold."""

EXTRA_TYPES: tuple[type, ...] = (Hello, ErrorAnswer)
"""Types outside any endpoint that a client still needs."""
