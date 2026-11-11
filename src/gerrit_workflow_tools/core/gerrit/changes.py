"""Cache-aware ChangeInfo operations on :class:`GerritService`: payloads, footer lookups, reviewers."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING, Any

from gerrit_workflow_tools.core.gerrit.cache_rows import dict_rows, payload_number, payload_triplet
from gerrit_workflow_tools.core.gerrit.change_queries import (
    batch_load_change_details,
    batch_load_changes_by_commit,
    change_freshness_key,
    probe_changes_updated,
)
from gerrit_workflow_tools.core.gerrit.change_view import ChangeView
from gerrit_workflow_tools.core.gerrit.rest import (
    LIGHT_QUERY_OPTIONS,
    LOG_QUERY_OPTIONS,
    change_id_for_gerrit_rest_path,
    parallel_map,
)
from gerrit_workflow_tools.core.gerrit.stack_context import StackContext


if TYPE_CHECKING:
    from gerrit_workflow_tools.core.gerrit.service import GerritService


# Up to this many moved changes are fetched by number; past it, asking the query again in full is one request too.
_FETCH_BY_NUMBER_LIMIT = 25


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
            trust_window_seconds=service.trust_window_seconds,
            refresh=service.refresh,
        )

    def query(self, query: str, *, n: int = 500) -> list[dict[str, Any]]:
        """ChangeInfo payloads matching *query*, in Gerrit's order, each with its full details.

        Gerrit is always asked what matches, but lightly: details come from the cache for
        every change whose freshness token did not move, so a listing that is polled costs
        one cheap request while nothing happens, and one more for what did change.
        """
        rest = self._service.rest
        light = dict_rows(rest.query_changes(query, n=n, options=list(LIGHT_QUERY_OPTIONS)))
        return self._with_details(
            light, lambda: dict_rows(rest.query_changes(query, n=n, options=list(LOG_QUERY_OPTIONS)))
        )

    def by_commit(self, shas: list[str]) -> list[dict[str, Any]]:
        """ChangeInfo payloads of the changes that have one of *shas* as a patchset.

        A commit that turned out to be no open change is remembered, so that not every
        listing of the chain that sits on it asks anew: for a week when it is a merged change
        (it stays one), for a quarter of an hour when Gerrit shows no change for it (history,
        or a change this user cannot see yet).
        """
        service = self._service
        settled = set() if service.refresh else service.cache.settled_commits(shas)
        ask = [sha for sha in shas if sha not in settled]
        if not ask:
            return []
        light = list(batch_load_changes_by_commit(service.rest, ask, options=list(LIGHT_QUERY_OPTIONS)).values())
        found = self._with_details(light, lambda: list(batch_load_changes_by_commit(service.rest, ask).values()))
        views = [ChangeView.from_payload(payload) for payload in found]
        pending = {sha for view in views if view.status != "MERGED" for sha in view.revisions}
        merged = {sha for view in views if view.status == "MERGED" for sha in view.revisions} - pending
        service.cache.settle_commits((sha for sha in ask if sha in merged), merged=True)
        service.cache.settle_commits((sha for sha in ask if sha not in merged | pending), merged=False)
        return found

    def _with_details(
        self, light: list[dict[str, Any]], ask_in_full: Callable[[], list[dict[str, Any]]]
    ) -> list[dict[str, Any]]:
        """The detailed payload for each row of a light listing, fetching only what the cache lacks or has stale."""
        service = self._service
        tokens = {key: change_freshness_key(row) for row in light if (key := payload_triplet(row))}
        current, moved = ({}, list(tokens)) if service.refresh else service.cache.reconcile(tokens)
        if moved:
            if len(moved) > _FETCH_BY_NUMBER_LIMIT:
                fetched = ask_in_full()
            else:
                gone = set(moved)
                numbers = [str(payload_number(row)) for row in light if payload_triplet(row) in gone]
                fetched = list(batch_load_change_details(service.rest, numbers).values())
            service.cache.upsert_changes(fetched)
            current.update({key: payload for payload in fetched if (key := payload_triplet(payload))})
        return [current[key] for key in tokens if key in current]

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
