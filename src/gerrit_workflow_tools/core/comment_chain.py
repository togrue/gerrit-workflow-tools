"""A comment chain as ger models it: its comments, whose turn it is, and where it sits."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Literal


if TYPE_CHECKING:
    from gerrit_workflow_tools.core.anchor_mapping import AnchorPlacement
    from gerrit_workflow_tools.core.comment_anchor import CommentRange
    from gerrit_workflow_tools.core.suggested_edits import SuggestedEdit


LocalCommentState = Literal["note", "local-draft"]
"""A comment that lives only in the local comment store."""
CommentState = Literal["note", "local-draft", "draft", "published"]


class ChainTurn(str, Enum):
    """Whose turn a comment chain is: what :func:`core.comment_chains.chain_turn` decides."""

    YOURS = "yours"
    """Somebody else spoke last: it waits on you."""
    DRAFT = "draft"
    """Somebody else spoke last and your answer is a draft: it waits on you to publish it."""
    THEIRS = "theirs"
    """Your published comment is the last word: it waits on the other side."""


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
    author_account_id: int | None = None
    """Who wrote it (Gerrit's ``author._account_id``): what tells your comments from others'."""
    context_lines: tuple[ContextLine, ...] = ()
    patch_set: int | None = None
    commit_id: str | None = None
    """The commit of that patch set (Gerrit's ``commit_id``): the version *line* counts in."""
    unresolved: bool = False
    """This comment's own flag. The chain takes the flag of its last comment."""
    draft: bool = False
    """A Gerrit draft: visible only to its author until published."""
    local: LocalCommentState | None = None
    """Set for a note or local draft: it is in the local comment store, not on Gerrit."""
    suggested_edits: tuple[SuggestedEdit, ...] = ()
    """Edits the comment suggests: Gerrit fix suggestions and ``suggestion`` blocks of its text."""
    updated: datetime | None = None
    """When it was written or last edited (Gerrit's ``updated``, UTC); ``None`` for a local comment."""

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
    comment_range: CommentRange | None = None
    """The characters a range comment was aimed at (Gerrit's ``range``); ``None`` for a line comment."""
    on_parent: bool = False
    """The chain sits on the parent side of the diff (Gerrit ``side: PARENT``)."""
    anchor: AnchorPlacement | None = None
    """A local comment's anchor, and where it lands on the file version shown."""
    turn: ChainTurn | None = None
    """Whose turn it is (:func:`core.comment_chains.chain_turn`); ``None`` for a local chain or an unknown user."""

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
