"""Inline comment chains of a change, published and draft, on :class:`GerritService`."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any, Literal

from gerrit_workflow_tools.core.comment_chains import CommentSelection, collect_comment_chains
from gerrit_workflow_tools.core.gerrit.cache import comments_context_key
from gerrit_workflow_tools.core.gerrit.rest import parallel_map
from gerrit_workflow_tools.core.gerrit.stack_context import ChangeResolutionError
from gerrit_workflow_tools.core.gerrit_change_status import CommentChain, InlineComment


if TYPE_CHECKING:
    from gerrit_workflow_tools.core.gerrit.service import GerritService


PUBLISH_GUARD_ENV = "GER_NO_PUBLISH"


def publish_blocked() -> bool:
    """True when :data:`PUBLISH_GUARD_ENV` forbids publishing drafts (agent environments set it).

    A guard against mistakes, not a security boundary: a process can change its own environment.
    """
    return os.environ.get(PUBLISH_GUARD_ENV, "").strip().lower() in ("1", "true", "yes")


@dataclass(frozen=True)
class ChainMatch:
    """A comment chain and the change it was found on."""

    change: str
    """REST key of the change (triplet or Change-Id)."""
    chain: CommentChain


@dataclass(frozen=True)
class DraftReply:
    """What :meth:`CommentApi.save_reply` wrote."""

    action: Literal["created", "updated"]
    draft_id: str | None
    patch_set: int | None
    unresolved: bool
    """The chain's state once this draft is published."""
    replaced_message: str | None = None
    """Text of the draft this one replaced; ``None`` when it was created."""


class CommentApi:
    """Comment chains for one change: published comments through the cache, drafts live."""

    def __init__(self, service: GerritService) -> None:
        self._service = service

    def get_chains(
        self,
        change_id: str,
        *,
        change_updated: str | None = None,
        context_padding: int | None = None,
        comments: CommentSelection = "unresolved",
    ) -> list[CommentChain]:
        """Comment chains of *change_id* selected by *comments*, with your drafts in their chains."""
        file_map = self.get_file_map(change_id, change_updated=change_updated, context_padding=context_padding)
        return collect_comment_chains(file_map, comments=comments, drafts=self.get_drafts(change_id))

    def chains_with_drafts(self, change_id: str, *, change_updated: str | None = None) -> list[CommentChain]:
        """The chains of *change_id* that hold a draft of yours: what a publish would send."""
        chains = self.get_chains(change_id, change_updated=change_updated, comments="all")
        return [chain for chain in chains if chain.draft is not None]

    def find_chain(self, changes: Mapping[str, str | None], comment_id: str) -> list[ChainMatch]:
        """Chains that contain *comment_id*, among *changes* (REST key -> freshness token).

        Any comment in a chain names the whole chain, root or reply, published or draft.
        """

        def matches(change: str, freshness: str | None) -> list[ChainMatch]:
            chains = self.get_chains(change, change_updated=freshness, comments="all")
            return [
                ChainMatch(change, chain) for chain in chains if any(c.comment_id == comment_id for c in chain.comments)
            ]

        found = parallel_map(partial(matches, change, freshness) for change, freshness in changes.items())
        return [match for per_change in found for match in per_change]

    def save_reply(self, change_id: str, chain: CommentChain, message: str, *, unresolved: bool | None) -> DraftReply:
        """Write your reply to *chain* as a draft; update the draft you already have there.

        The draft answers the newest published comment, on that comment's patchset and
        side, as the web UI does. *unresolved* ``None`` keeps your existing draft's flag,
        else inherits the answered comment's: an open chain stays open.
        """
        answered, existing = chain.answered, chain.draft
        basis = existing or answered
        flag = basis.unresolved if unresolved is None else unresolved
        body: dict[str, Any] = {"path": chain.path, "message": message, "unresolved": flag}
        if answered.comment_id and not answered.draft:
            body["in_reply_to"] = answered.comment_id
        if chain.line is not None:
            body["line"] = chain.line
        if chain.on_parent:
            body["side"] = "PARENT"
        revision = basis.patch_set if basis.patch_set is not None else "current"
        row = self._service.rest.put_draft(
            change_id, revision, body, draft_id=existing.comment_id if existing else None
        )
        draft_id = row.get("id")
        return DraftReply(
            action="updated" if existing else "created",
            draft_id=draft_id if isinstance(draft_id, str) else None,
            patch_set=basis.patch_set,
            unresolved=flag,
            replaced_message=existing.message if existing else None,
        )

    def create_draft(self, change_id: str, patch_set: int, comment: Mapping[str, Any]) -> str | None:
        """Start a new chain: a draft *comment* (CommentInput fields) on *patch_set*; return its id."""
        row = self._service.rest.put_draft(change_id, patch_set, dict(comment))
        draft_id = row.get("id")
        return draft_id if isinstance(draft_id, str) else None

    def discard_draft(self, change_id: str, draft_id: str) -> InlineComment | None:
        """Delete your draft *draft_id* on any patchset of *change_id* and return it; ``None`` if absent."""
        for chain in self.get_chains(change_id, comments="all"):
            for comment in chain.comments:
                if comment.draft and comment.comment_id == draft_id:
                    revision = comment.patch_set if comment.patch_set is not None else "current"
                    self._service.rest.delete_draft(change_id, revision, draft_id)
                    return comment
        return None

    def discard_reply(self, change_id: str, chain: CommentChain) -> InlineComment:
        """Delete your draft in *chain* and return it. A chain without one is not found."""
        existing = chain.draft
        if existing is None or existing.comment_id is None:
            raise ChangeResolutionError(f"no draft of yours in the chain at {chain.path}")
        revision = existing.patch_set if existing.patch_set is not None else "current"
        self._service.rest.delete_draft(change_id, revision, existing.comment_id)
        return existing

    def publish_drafts(
        self, change_id: str, *, message: str | None = None, labels: Mapping[str, int] | None = None
    ) -> None:
        """Publish your drafts on *change_id*, with *labels* voted, then drop its cached rows.

        Publishing moves the change's meta ref, but inside the trust window the probe is
        skipped; invalidating makes the next read see the published chains straight away.
        Callers check :func:`publish_blocked` first: the guard is policy, this is the act.
        """
        service = self._service
        service.rest.publish_drafts(change_id, message=message, labels=labels)
        service.cache.invalidate_changes([service.cache_key(change_id)])

    def get_drafts(self, change_id: str) -> dict[str, list[dict[str, Any]]]:
        """Your drafts on every patchset of *change_id*, always fetched.

        Never cached: drafts live in All-Users, not in the change's NoteDb meta ref, so the
        follow-up freshness token (ADR-0007) does not move when a draft does.
        """
        return self._service.rest.list_drafts(change_id)

    def get_file_map(
        self,
        change_id: str,
        *,
        change_updated: str | None = None,
        context_padding: int | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Return raw published Gerrit comments grouped by file path."""

        rest = self._service.rest
        fetch = rest.get_comments
        if context_padding is not None:
            fetch = partial(rest.get_comments, enable_context=True, context_padding=context_padding)

        return self._service.cache.load_comments(
            change_id,
            fetch_comments=fetch,
            change_updated=change_updated,
            trust_window_seconds=self._service.trust_window_seconds,
            refresh=self._service.refresh,
            context_key=comments_context_key(context_padding),
        )
