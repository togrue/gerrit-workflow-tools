"""Cache-aware ChangeInfo operations on :class:`GerritService`: payloads, footer lookups, reviewers."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Any

from gerrit_workflow_tools.core.gerrit.change_queries import (
    batch_load_change_details,
    delta_changes_since,
    probe_changes_updated,
)
from gerrit_workflow_tools.core.gerrit.rest import change_id_for_gerrit_rest_path, parallel_map
from gerrit_workflow_tools.core.gerrit.stack_context import StackContext


if TYPE_CHECKING:
    from gerrit_workflow_tools.core.gerrit.service import GerritService


class ChangeApi:
    """Cache-aware ChangeInfo operations."""

    def __init__(self, service: GerritService) -> None:
        self._service = service

    def find_by_footer_change_ids(self, change_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        """Return already-known ChangeInfo rows grouped by footer Change-Id, across all branches.

        Answers from local state only — this never contacts Gerrit. It relies on a prior
        :meth:`get_payloads` having stored every branch carrying these Change-Ids, which the
        stack overlay's project-scoped ``OR`` batch does as a side effect. Call it after the
        overlay, not before: on a cold cache it returns nothing rather than fetching.

        Used to derive multi-branch resolution notes without a per-Change-Id query storm.
        """

        return self._service.cache.find_payloads_by_footer_change_ids(change_ids)

    def get_payloads(self, change_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Return raw ChangeInfo payloads keyed by Gerrit triplet ``id``."""

        service = self._service
        stack: StackContext | None
        try:
            stack = service.stack_context()
        except Exception:  # pylint: disable=broad-exception-caught
            stack = None

        return service.cache.load_changes(
            [service.cache_key(ref, stack) for ref in change_ids],
            probe_updated=partial(probe_changes_updated, service.rest),
            fetch_changes=partial(batch_load_change_details, service.rest),
            fetch_delta=partial(delta_changes_since, service.rest, stack.project) if stack else None,
            scope_key=f"{service.cache.host}~{stack.project}" if stack else None,
            trust_window_seconds=service.trust_window_seconds,
            refresh=service.refresh,
        )

    def set_reviewers(
        self,
        change_id: str,
        *,
        add: list[str] | None = None,
        remove: list[int] | None = None,
        ccs: list[str] | None = None,
    ) -> None:
        """Add reviewers, then remove reviewers, via REST, and cache fresh ChangeInfo.

        Adds run first because Gerrit rejects an unknown reviewer: a failed add must leave the
        existing reviewers in place. Any failure, in the writes or the re-read, drops the
        cached row instead.
        """
        service = self._service
        rest_ref = change_id_for_gerrit_rest_path(change_id)
        cache_key = service.cache_key(change_id)
        service.cache.invalidate_changes([cache_key])
        if add or ccs:
            service.rest.set_reviewers_batch(rest_ref, reviewers=add or [], ccs=ccs or [])
        parallel_map([partial(service.rest.delete_reviewer, rest_ref, account_id) for account_id in remove or []])
        service.cache.upsert_changes({cache_key: service.rest.get_change(rest_ref)})
