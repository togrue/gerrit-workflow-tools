"""High-level Gerrit API service with cache-aware batch operations."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.cache import (
    DEFAULT_CHANGE_TRUST_WINDOW_SECONDS,
    GerritCache,
    comments_context_key,
)
from gerrit_workflow_tools.core.gerrit.change_view import ChangeView
from gerrit_workflow_tools.core.gerrit.rest import (
    GerritApiError,
    GerritRest,
    HttpGerritRest,
    batch_load_change_details,
    batch_load_changes_by_commit,
    change_id_for_gerrit_rest_path,
    delta_changes_since,
    parallel_map,
    probe_changes_updated,
    resolve_gerrit_web_base,
)
from gerrit_workflow_tools.core.gerrit.stack_context import StackContext, build_triplet, resolve_stack_context
from gerrit_workflow_tools.core.review_chain import (
    INBOX_QUERY_OPTIONS,
    ReviewChain,
    assemble_review_chains,
    missing_parent_shas,
)


logger = logging.getLogger(__name__)

# Host capability key: does this Gerrit serve the Checks plugin endpoint?
_CHECKS_CAPABILITY = "checks"


class GerritService:
    """High-level object API layered on cached, parallel Gerrit REST access."""

    def __init__(
        self,
        rest: GerritRest,
        cache: GerritCache,
        *,
        trust_window_seconds: int = DEFAULT_CHANGE_TRUST_WINDOW_SECONDS,
        refresh: bool = False,
        cwd: Path | str | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.rest = rest
        self.cache = cache
        # Settings for *cwd*, read once. Explicit for the same reason cwd is: nothing
        # below this layer may reach for process-wide configuration.
        self.settings = settings if settings is not None else Settings.from_cwd(cwd)
        # The repo this service reports on. Explicit, because GerritRest has no cwd to
        # scavenge it from; defaults to the process cwd for callers that don't care.
        self.cwd = str(cwd) if cwd is not None else os.getcwd()
        self.trust_window_seconds = trust_window_seconds
        self.refresh = refresh
        # Whether this host serves the Checks plugin: True / False / None when not yet
        # known. A property of the host, not of any change, so it is answered from the
        # cache and only rediscovered once that verdict expires.
        self._checks_endpoint_available: bool | None = None
        self.changes = ChangeApi(self)
        self.comments = CommentApi(self)

    @classmethod
    def from_cwd(
        cls,
        cwd: Path | str | None,
        *,
        settings: Settings | None = None,
        refresh: bool = False,
        trust_window_seconds: int = DEFAULT_CHANGE_TRUST_WINDOW_SECONDS,
        rest: GerritRest | None = None,
        cache: GerritCache | None = None,
    ) -> GerritService:
        """Construct a service from git-configured Gerrit settings.

        Pass *rest* to supply the Gerrit implementation instead of building an
        :class:`HttpGerritRest`. Doing so also skips ``gerrit.webUrl`` resolution — the web
        base comes from the implementation — so callers that inject need no Gerrit config.
        *settings* defaults to one read from
        *cwd*; pass it when the caller has already taken a snapshot, so the whole command
        works from a single ``git config --list --show-scope``.
        """

        if os.environ.get("GER_CACHE_REFRESH", "").strip().lower() in ("1", "true", "yes"):
            refresh = True
        resolved = settings if settings is not None else Settings.from_cwd(cwd)
        if rest is None:
            web_base = resolve_gerrit_web_base(resolved)
            rest = HttpGerritRest.from_settings(web_base, resolved)
        else:
            web_base = rest.web_base
        return cls(
            rest,
            cache if cache is not None else GerritCache.for_web_base(web_base),
            refresh=refresh,
            trust_window_seconds=trust_window_seconds,
            cwd=cwd,
            settings=resolved,
        )

    @property
    def web_base(self) -> str:
        """Configured Gerrit web base."""

        return self.rest.web_base

    def stack_context(self) -> StackContext:
        """The stack context for this service (memoised by :func:`resolve_stack_context`)."""
        return resolve_stack_context(self.cwd, settings=self.settings)

    def cache_key(self, change_ref: str, ctx: StackContext | None = None) -> str:
        """Map a change ref to its cache key: triplets and numbers verbatim, Change-Ids via the stack context."""
        if "~" in change_ref or change_ref.isdigit():
            return change_ref
        ctx = ctx or self.stack_context()
        return build_triplet(ctx.project, ctx.push_branch, change_ref)

    def fetch_gerrit_data(
        self,
        commits: list[Any],
        *,
        cwd: Path | str | None = None,
        fetch_ci_pipelines: bool = False,
    ) -> list[Any]:
        """Return ``LogCommit`` rows enriched with cached Gerrit data and parallel follow-ups."""

        from gerrit_workflow_tools.core.gerrit_change_status import build_log_commit

        stack = self.stack_context()

        change_ids = [row.valid_change_id for row in commits if row.valid_change_id]
        detail_map = self.changes.get_payloads(change_ids) if change_ids else {}
        by_triplet: dict[str, ChangeView] = {}
        for ref, payload in detail_map.items():
            view = ChangeView.from_payload(payload)
            by_triplet[ref] = view
            if view.key:
                by_triplet[view.key] = view

        result: list[Any] = []
        follow_ups: list[Callable[[], None]] = []
        for row in commits:
            change: ChangeView | None = None
            if row.valid_change_id:
                change = by_triplet.get(build_triplet(stack.project, stack.push_branch, row.valid_change_id))
            lc, needed = build_log_commit(row, change, self.web_base, cwd)
            if fetch_ci_pipelines and lc.pushed:
                needed = needed | frozenset({"checks"})
            result.append(lc)
            if needed and change is not None and change.key:
                follow_ups.append(partial(self._fill_follow_ups, lc, change, needed, stack.project, fetch_ci_pipelines))

        parallel_map(follow_ups)
        return result

    def _fill_follow_ups(
        self,
        commit: Any,
        change: ChangeView,
        kinds: frozenset[str],
        project: str,
        fetch_pipelines: bool,
    ) -> None:
        """Fill what *change*'s payload lacked. A failed follow-up leaves its fields as they were."""
        from gerrit_workflow_tools.core.comment_chains import count_unresolved_in_file_map
        from gerrit_workflow_tools.core.reviewer import reviewer_accounts_from_reviewer_list

        def _comments() -> None:
            file_map = self.comments.get_file_map(change.key, change_updated=change.freshness)
            commit.comments_unresolved = count_unresolved_in_file_map(file_map)

        def _checks() -> None:
            commit.ci_failures, commit.ci_links, commit.ci_pipelines = self._fetch_ci_result(
                change.key,
                project=project,
                change_updated=change.freshness,
                current_revision_number=change.current_revision_number,
                fetch_pipelines=fetch_pipelines,
            )

        def _reviewers() -> None:
            commit.reviewers = reviewer_accounts_from_reviewer_list(self.rest.list_change_reviewers(change.key))

        for kind, fill in (("comments", _comments), ("checks", _checks), ("reviewers", _reviewers)):
            if kind in kinds:
                try:
                    fill()
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    logger.debug("%s follow-up failed for %s: %s", kind, change.key, exc)

    def fetch_review_chains(  # pylint: disable=too-many-locals
        self,
        query: str,
        *,
        now: datetime | None = None,
        self_account_id: int | None = None,
    ) -> list[ReviewChain]:
        """Query Gerrit and assemble **review chains**. Never resolves stack context.

        The inbox is scoped to a Gerrit host, not a working directory: this path
        must not call :func:`resolve_stack_context`. Live query, then one batched
        ``commit:<sha>`` follow-up for parents missing from the result set.
        """
        moment = now if now is not None else datetime.now(timezone.utc)
        queried = self.rest.query_changes(query, n=500, options=list(INBOX_QUERY_OPTIONS))
        rows = [ChangeView.from_payload(row) for row in queried if isinstance(row, dict)]
        account_id = self_account_id
        if account_id is None:
            try:
                raw = self.rest.get_account("self").get("_account_id")
                account_id = raw if isinstance(raw, int) else None
            except GerritApiError:
                account_id = None
        parent_shas = missing_parent_shas(rows)
        follow_up_rows: list[ChangeView] = []
        unmatched: set[str] = set()
        if parent_shas:
            fetched = batch_load_changes_by_commit(self.rest, parent_shas, options=list(INBOX_QUERY_OPTIONS))
            follow_up_rows = [ChangeView.from_payload(payload) for payload in fetched.values()]
            current_shas = {change.current_revision for change in follow_up_rows if change.current_revision}
            for sha in parent_shas:
                # A parent found only as an older patch set: the chain is partial.
                if sha not in current_shas and any(sha in change.revisions for change in follow_up_rows):
                    unmatched.add(sha)
        return assemble_review_chains(
            rows,
            follow_up_rows,
            web_base=self.web_base,
            now=moment,
            self_account_id=account_id,
            follow_up_unmatched=unmatched,
        )

    def _get_messages_or_empty(self, change_id: str) -> list[dict[str, Any]]:
        try:
            return self.rest.get_messages(change_id)
        except GerritApiError:
            return []

    def _checks_or_empty(self, change_id: str) -> list[dict[str, Any]]:
        """Checks-plugin rows for *change_id*, or ``[]``.

        A host without the Checks plugin answers 404 for every CI-failed change, on every
        run. Whether the endpoint exists is a fact about the *host*, not the change, so the
        verdict is cached: the first run pays ``k`` 404s, later runs pay none.

        An in-run memo alone would not help — the follow-ups run concurrently, so they all
        ask before any 404 comes back. Only persistence removes the calls.

        Other errors are per-change and must never disable the endpoint.
        """

        if self._checks_endpoint_available is None:
            self._checks_endpoint_available = self.cache.capability(_CHECKS_CAPABILITY)
        if self._checks_endpoint_available is False:
            return []
        try:
            rows = self.rest.get_checks(change_id)
        except GerritApiError as exc:
            if exc.status == 404:
                self._checks_endpoint_available = False
                self.cache.set_capability(_CHECKS_CAPABILITY, False)
                logger.debug("no Checks plugin on %s; skipping further checks calls", self.web_base)
            return []
        if self._checks_endpoint_available is None:
            self._checks_endpoint_available = True
            self.cache.set_capability(_CHECKS_CAPABILITY, True)
        return rows

    def _fetch_ci_result(
        self,
        change_id: str,
        *,
        project: str,
        change_updated: str | None = None,
        current_revision_number: int | None = None,
        fetch_pipelines: bool = False,
    ) -> tuple[list[str], list, list]:
        """Return failed CI names, transformed links, and Checks-plugin pipelines."""

        from gerrit_workflow_tools.core.ci_links import ci_pipelines_from_checks, failed_check_names
        from gerrit_workflow_tools.core.ci_strategy import LazyRows, extract_ci_links_via_registry
        from gerrit_workflow_tools.core.gerrit_message_parsing import ci_pipelines_from_build_messages

        def _fetch_checks_and_messages(triplet: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
            check_rows = self._checks_or_empty(triplet)
            if check_rows:
                return check_rows, []
            return [], self._get_messages_or_empty(triplet)

        check_rows, message_rows = self.cache.load_checks(
            change_id,
            fetch_checks=_fetch_checks_and_messages,
            change_updated=change_updated,
            trust_window_seconds=self.trust_window_seconds,
            refresh=self.refresh,
        )
        names = failed_check_names(check_rows)

        links: list = []
        try:
            if check_rows and not message_rows:
                messages_arg: Sequence[Mapping[str, Any]] | LazyRows = LazyRows(
                    fetch=lambda: self._get_messages_or_empty(change_id)
                )
            else:
                messages_arg = message_rows
            links = extract_ci_links_via_registry(
                self.cwd,
                project=project,
                checks=check_rows,
                messages=messages_arg,
                settings=self.settings,
                web_base=self.web_base,
                current_revision_number=current_revision_number,
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.debug("CI strategy failed for %s (%s): %s", change_id, project, exc)

        if fetch_pipelines:
            if check_rows:
                pipelines = ci_pipelines_from_checks(check_rows, links)
            else:
                pipelines = ci_pipelines_from_build_messages(
                    message_rows if message_rows else self._get_messages_or_empty(change_id),
                    current_revision_number=current_revision_number,
                    include_outdated_fallback=True,
                )
        else:
            pipelines = []
        return names, links, pipelines


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
