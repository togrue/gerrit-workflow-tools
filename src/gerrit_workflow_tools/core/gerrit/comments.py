"""Inline comment chains of a change, published and draft, on :class:`GerritService`."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Any

from gerrit_workflow_tools.core.comment_chains import CommentSelection, collect_comment_chains
from gerrit_workflow_tools.core.gerrit.cache import comments_context_key
from gerrit_workflow_tools.core.gerrit_change_status import CommentChain


if TYPE_CHECKING:
    from gerrit_workflow_tools.core.gerrit.service import GerritService


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
