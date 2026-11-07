"""Cache-aware inline comment operations on :class:`GerritService`."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Any

from gerrit_workflow_tools.core.gerrit.cache import comments_context_key


if TYPE_CHECKING:
    from gerrit_workflow_tools.core.gerrit.service import GerritService


class CommentApi:
    """Cache-aware inline comment operations."""

    def __init__(self, service: GerritService) -> None:
        self._service = service

    def get_file_map(
        self,
        change_id: str,
        *,
        change_updated: str | None = None,
        context_padding: int | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Return raw Gerrit comments grouped by file path."""

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
