"""The ``ger serve`` contract for what a client sends: the query of each call, and the body of each action.

A query flag is sent as ``1``; a list as the parameter repeated. Part of the contract
:mod:`~gerrit_workflow_tools.server.contract` gathers.
"""

from __future__ import annotations

from typing import Literal

from typing_extensions import NotRequired, TypedDict

from gerrit_workflow_tools.core.comment_chains import CommentSelection
from gerrit_workflow_tools.core.review_checkout import ReviewMode
from gerrit_workflow_tools.server.contract_commit import NewCommentState
from gerrit_workflow_tools.server.contract_stack import StackStep


# --- Queries and bodies -----------------------------------------------------------------------


class Query(TypedDict):
    refresh: NotRequired[bool]
    """Read the local stack and Gerrit again, inside the trust window."""


class ActivityQuery(TypedDict):
    since: NotRequired[int]
    """Answer once the sequence moves past this; at once without it."""
    wait: NotRequired[int]
    """Seconds to wait at most."""


class OverviewQuery(Query):
    all: NotRequired[bool]
    """Every project, not only this repository's."""
    pipelines: NotRequired[bool]


class StackQuery(Query):
    pipelines: NotRequired[bool]


class ShowQuery(Query):
    rev: NotRequired[list[str]]
    stack: NotRequired[bool]
    chain: NotRequired[bool]
    """The ``rev`` values are one review chain, base first."""
    ci: NotRequired[bool]
    comments: NotRequired[CommentSelection]
    context: NotRequired[int]
    markdown: NotRequired[bool]
    """Add ``markdown``: what ``ger show --ai`` prints."""
    files: NotRequired[bool]
    """Add ``files``: the commit's files against its parent, as ``/diff`` answers them."""
    pipelines: NotRequired[bool]


class DiffQuery(Query):
    rev: str
    patchset: NotRequired[int]
    """This patchset of the change, not the commit ``rev`` names."""
    since: NotRequired[Literal["reviewed", "current"] | int]
    """A review delta from the patchset you reviewed, Gerrit's current one, or patchset ``n``."""
    local: NotRequired[str]
    """The review delta runs to this local commit."""


class RevQuery(Query):
    rev: str


class RunbookQuery(Query):
    rev: NotRequired[str]
    """Narrow the runbook to this commit of the stack."""


class FileQuery(Query):
    rev: str
    """A commit id, as ``/diff`` returns it."""
    path: str


class NoBody(TypedDict):
    pass


class ReviewOpenBody(TypedDict):
    change: int | str
    patchset: NotRequired[int]
    mode: NotRequired[ReviewMode]
    stage: NotRequired[bool]
    force: NotRequired[bool]


class ReplyBody(TypedDict):
    rev: str
    comment_id: str
    """Any comment of the chain."""
    message: str
    resolved: NotRequired[bool]
    """Omit to leave the chain as it is."""


class NewCommentBody(TypedDict):
    rev: str
    location: str
    """``PATH``, ``PATH:LINE``, ``PATH:START-END`` or ``PATH:L.C-L.C``, lines from 1."""
    message: str
    state: NotRequired[NewCommentState]
    resolved: NotRequired[bool]


class EditCommentBody(TypedDict):
    rev: str
    comment_id: str
    message: str
    resolved: NotRequired[bool]


class DiscardCommentBody(TypedDict):
    rev: str
    comment_id: str


class HoldBody(TypedDict):
    root_id: str
    held: bool


class UpstreamBody(TypedDict):
    upstream: str
    """A remote-tracking branch: ``origin/main``."""


class RewriteBody(TypedDict):
    steps: list[StackStep]
    """Oldest first."""
    autostash: NotRequired[bool]
    """Stash uncommitted changes for the rewrite; without it they are refused (``uncommitted-changes``)."""


class RewordBody(TypedDict):
    rev: str
    message: str


class UndoBody(TypedDict):
    head: str
    """The stack's head after the rewrite."""
    previous_head: str
