"""High-level Gerrit API service with cache-aware batch operations."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.cache import (
    DEFAULT_ACCOUNT_TTL_SECONDS,
    DEFAULT_CHANGE_TRUST_WINDOW_SECONDS,
    GerritCache,
)
from gerrit_workflow_tools.core.gerrit.change_resolution import StackContext, build_triplet, resolve_stack_context
from gerrit_workflow_tools.core.gerrit.models import Account, Change, Comment
from gerrit_workflow_tools.core.gerrit.rest import (
    GerritApiError,
    GerritRest,
    HttpGerritRest,
    alias_batch_fetch_results,
    batch_load_change_details,
    batch_load_changes_by_commit,
    change_id_for_gerrit_rest_path,
    delta_changes_since,
    parallel_map,
    probe_changes_updated,
    resolve_gerrit_web_base,
)
from gerrit_workflow_tools.core.review_chain import (
    INBOX_QUERY_OPTIONS,
    ReviewChain,
    assemble_review_chains,
    current_revision_sha,
    missing_parent_shas,
)


logger = logging.getLogger(__name__)

# Host capability key: does this Gerrit serve the Checks plugin endpoint?
_CHECKS_CAPABILITY = "checks"


def _service_stack_context(service: GerritService) -> StackContext:
    """Resolve stack context once per service instance (batch paths may call this many times)."""
    cached = getattr(service, "_stack_context", None)
    if cached is None:
        cached = resolve_stack_context(service.cwd, settings=service.settings)
        service._stack_context = cached
    return cached


def _batch_ref_for_change_key(
    service: GerritService,
    change_key: str,
    *,
    stack: StackContext | None = None,
) -> str:
    """Map a change ref to a triplet or numeric ref for cache and batch REST queries."""
    if "~" in change_key:
        return change_key
    if change_key.isdigit():
        return change_key
    ctx = stack if stack is not None else _service_stack_context(service)
    return build_triplet(ctx.project, ctx.push_branch, change_key)


def _cache_triplets(service: GerritService, change_refs: list[str]) -> list[str]:
    """Map change refs to cache keys, resolving stack context at most once."""
    stack: StackContext | None = None
    triplets: list[str] = []
    for ref in change_refs:
        if "~" in ref or ref.isdigit():
            triplets.append(ref)
            continue
        if stack is None:
            stack = _service_stack_context(service)
        triplets.append(build_triplet(stack.project, stack.push_branch, ref))
    return triplets


def _cache_triplet(service: GerritService, change_ref: str) -> str:
    """Return the verbatim cache key (triplet or numeric id) for *change_ref*."""
    return _batch_ref_for_change_key(service, change_ref)


class GerritService:
    """High-level object API layered on cached, parallel Gerrit REST access."""

    def __init__(
        self,
        rest: GerritRest,
        cache: GerritCache,
        *,
        trust_window_seconds: int = DEFAULT_CHANGE_TRUST_WINDOW_SECONDS,
        account_ttl_seconds: int = DEFAULT_ACCOUNT_TTL_SECONDS,
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
        self.account_ttl_seconds = account_ttl_seconds
        self.refresh = refresh
        # Whether this host serves the Checks plugin: True / False / None when not yet
        # known. A property of the host, not of any change, so it is answered from the
        # cache and only rediscovered once that verdict expires.
        self._checks_endpoint_available: bool | None = None
        self._stack_context: StackContext | None = None
        self.changes = ChangeApi(self)
        self.accounts = AccountApi(self)
        self.comments = CommentApi(self)
        self.checks = ChecksApi(self)

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
        base comes from the implementation — so callers that inject need no Gerrit config,
        so callers that inject need no Gerrit config. *settings* defaults to one read from
        *cwd*; pass it when the caller has already taken a snapshot, so the whole command
        works from a single ``git config --list``.
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

    def _fetch_change_payloads(self, triplets: list[str]) -> dict[str, dict[str, Any]]:
        """Batch-fetch ChangeInfo for *triplets* via project-scoped Change-Id OR.

        Gerrit may return extra rows (same Change-Id on other branches); those are
        kept under their ``id`` and aliased onto the requested target-branch keys.
        """
        fetched = batch_load_change_details(self.rest, triplets)
        return alias_batch_fetch_results(triplets, fetched)

    def _probe_changes_updated(self, triplets: list[str]) -> dict[str, str]:
        return probe_changes_updated(self.rest, triplets)

    def _fetch_delta(self, project: str, since: str) -> tuple[list[dict[str, Any]], bool]:
        return delta_changes_since(self.rest, project, since)

    def _fetch_account_payloads(self, account_ids: list[int | str]) -> dict[int, dict[str, Any]]:
        def _one(account_id: int | str) -> tuple[int, dict[str, Any]]:
            payload = self.rest.get_account(account_id)
            raw = payload.get("_account_id")
            resolved_id = raw if isinstance(raw, int) else int(account_id)
            return resolved_id, payload

        def _account_job(account_id: int | str) -> Callable[[], tuple[int, dict[str, Any]]]:
            def _job() -> tuple[int, dict[str, Any]]:
                return _one(account_id)

            return _job

        jobs = [_account_job(account_id) for account_id in account_ids]
        return dict(parallel_map(jobs))

    def fetch_gerrit_data(
        self,
        commits: list[Any],
        *,
        cwd: Path | str | None = None,
        fetch_ci_pipelines: bool = False,
    ) -> list[Any]:
        """Return ``LogCommit`` rows enriched with cached Gerrit data and parallel follow-ups."""

        from gerrit_workflow_tools.core.comment_chains import count_unresolved_in_file_map
        from gerrit_workflow_tools.core.gerrit_change_status import build_log_commit, current_revision_number
        from gerrit_workflow_tools.core.reviewer import reviewer_accounts_from_reviewer_list

        stack = _service_stack_context(self)

        change_ids = [row.change_id for row in commits if row.change_id]
        detail_map = self.changes.get_payloads(change_ids) if change_ids else {}
        detail_by_triplet: dict[str, dict[str, Any]] = {}
        for ref, payload in detail_map.items():
            detail_by_triplet[ref] = payload
            triplet = payload.get("id")
            if isinstance(triplet, str) and triplet:
                detail_by_triplet[triplet] = payload

        result: list[Any] = []
        # (row index, follow-up kinds, triplet, change.updated) — `updated` is the cache
        # validity key for every follow-up, so it travels with the job.
        pending: list[tuple[int, frozenset[str], str, str | None, int | None, bool]] = []

        for row in commits:
            detail = None
            if row.change_id:
                triplet = build_triplet(stack.project, stack.push_branch, row.change_id)
                detail = detail_by_triplet.get(triplet)
            lc, needed = build_log_commit(row, detail, self.web_base, cwd)
            if fetch_ci_pipelines and lc.pushed:
                needed = needed | frozenset({"checks"})
            result.append(lc)
            if needed and detail is not None:
                triplet = detail.get("id")
                if isinstance(triplet, str) and triplet:
                    rev_num = current_revision_number(detail)
                    pending.append((len(result) - 1, needed, triplet, lc.updated, rev_num, fetch_ci_pipelines))

        if not pending:
            return result

        def _follow_up(
            item: tuple[int, frozenset[str], str, str | None, int | None, bool],
        ) -> tuple[int, dict[str, Any]]:
            idx, kinds, triplet, change_updated, rev_num, want_pipelines = item
            updates: dict[str, Any] = {}
            if "comments" in kinds:
                try:
                    file_map = self.comments.get_file_map(triplet, change_updated=change_updated)
                    updates["comments"] = count_unresolved_in_file_map(file_map)
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    logger.debug("comments follow-up failed for %s: %s", triplet, exc)
            if "checks" in kinds:
                try:
                    names, links, pipelines = self._fetch_ci_result(
                        triplet,
                        project=stack.project,
                        change_updated=change_updated,
                        current_revision_number=rev_num,
                        fetch_pipelines=want_pipelines,
                    )
                    updates["checks"] = names
                    updates["ci_links"] = links
                    updates["ci_pipelines"] = pipelines
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    logger.debug("checks follow-up failed for %s: %s", triplet, exc)
            if "reviewers" in kinds:
                try:
                    rows = self.rest.list_change_reviewers(triplet)
                    updates["reviewers"] = reviewer_accounts_from_reviewer_list(rows)
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    logger.debug("reviewers follow-up failed for %s: %s", triplet, exc)
            return idx, updates

        jobs = [lambda item=item: _follow_up(item) for item in pending]
        for idx, updates in parallel_map(jobs):
            if "comments" in updates:
                result[idx].comments_unresolved = updates["comments"]
            if "checks" in updates:
                result[idx].ci_failures = updates["checks"]
            if "ci_links" in updates:
                result[idx].ci_links = updates["ci_links"]
            if "ci_pipelines" in updates:
                result[idx].ci_pipelines = updates["ci_pipelines"]
            if "reviewers" in updates:
                result[idx].reviewers = updates["reviewers"]

        return result

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
        rows = [row for row in queried if isinstance(row, dict)]
        account_id = self_account_id
        if account_id is None:
            try:
                raw = self.rest.get_account("self").get("_account_id")
                account_id = raw if isinstance(raw, int) else None
            except GerritApiError:
                account_id = None
        parent_shas = missing_parent_shas(rows)
        follow_up_rows: list[dict[str, Any]] = []
        unmatched: set[str] = set()
        if parent_shas:
            fetched = batch_load_changes_by_commit(self.rest, parent_shas, options=list(INBOX_QUERY_OPTIONS))
            by_current: dict[str, dict[str, Any]] = {}
            for payload in fetched.values():
                follow_up_rows.append(payload)
                sha = current_revision_sha(payload)
                if sha:
                    by_current[sha] = payload
            for sha in parent_shas:
                if sha in by_current:
                    continue
                for payload in fetched.values():
                    revisions = payload.get("revisions")
                    if isinstance(revisions, dict) and any(
                        isinstance(key, str) and key.lower() == sha for key in revisions
                    ):
                        unmatched.add(sha)
                        break
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

        check_rows, message_rows = self.checks.get_rows(
            change_id,
            fetch_checks=_fetch_checks_and_messages,
            change_updated=change_updated,
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

    def _refresh_after_mutation(self, change_id: str) -> Change:
        rest_ref = change_id_for_gerrit_rest_path(change_id)
        cache_key = _cache_triplet(self, change_id)
        try:
            payload = self.rest.get_change(rest_ref)
        except GerritApiError:
            self.cache.invalidate_changes([cache_key])
            raise
        self.cache.invalidate_changes([cache_key])
        self.cache.upsert_changes({cache_key: payload})
        return Change(payload)


class ChangeApi:
    """Cache-aware ChangeInfo operations."""

    def __init__(self, service: GerritService) -> None:
        self._service = service

    def get(self, change_id: str) -> Change:
        """Return one change or raise if Gerrit did not return it."""

        triplet = _cache_triplet(self._service, change_id)
        payloads = self._service.cache.load_changes(
            [triplet],
            probe_updated=self._service._probe_changes_updated,
            fetch_changes=self._service._fetch_change_payloads,
            trust_window_seconds=self._service.trust_window_seconds,
            refresh=self._service.refresh,
        )
        payload = payloads.get(triplet)
        if payload is None:
            raise GerritApiError(f"no matching change {change_id}")
        return Change(payload)

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

        triplets = _cache_triplets(self._service, change_ids)
        scope_key: str | None = None
        fetch_delta: Callable[[str], tuple[list[dict[str, Any]], bool]] | None = None
        try:
            stack = _service_stack_context(self._service)
            scope_key = f"{self._service.cache.host}~{stack.project}"
            project = stack.project

            def _fetch_delta(since: str) -> tuple[list[dict[str, Any]], bool]:
                return self._service._fetch_delta(project, since)

            fetch_delta = _fetch_delta
        except Exception:  # pylint: disable=broad-exception-caught
            scope_key = None
            fetch_delta = None

        return self._service.cache.load_changes(
            triplets,
            probe_updated=self._service._probe_changes_updated,
            fetch_changes=self._service._fetch_change_payloads,
            fetch_delta=fetch_delta,
            scope_key=scope_key,
            trust_window_seconds=self._service.trust_window_seconds,
            refresh=self._service.refresh,
        )

    def set_topic(self, change_id: str, topic: str | None) -> Change:
        """Set or clear the change topic via REST and update cache."""

        rest_ref = change_id_for_gerrit_rest_path(change_id)
        cache_key = _cache_triplet(self._service, change_id)
        try:
            self._service.rest.set_topic(rest_ref, topic)
            return self._service._refresh_after_mutation(change_id)
        except GerritApiError:
            self._service.cache.invalidate_changes([cache_key])
            raise

    def set_wip(self, change_id: str, on: bool) -> Change:
        """Set or clear work-in-progress state via REST and update cache."""

        rest_ref = change_id_for_gerrit_rest_path(change_id)
        cache_key = _cache_triplet(self._service, change_id)
        try:
            self._service.rest.set_wip(rest_ref, on)
            return self._service._refresh_after_mutation(change_id)
        except GerritApiError:
            self._service.cache.invalidate_changes([cache_key])
            raise

    def set_private(self, change_id: str, on: bool) -> Change:
        """Set or clear private state via REST and update cache."""

        rest_ref = change_id_for_gerrit_rest_path(change_id)
        cache_key = _cache_triplet(self._service, change_id)
        try:
            self._service.rest.set_private(rest_ref, on)
            return self._service._refresh_after_mutation(change_id)
        except GerritApiError:
            self._service.cache.invalidate_changes([cache_key])
            raise

    def set_reviewers(
        self,
        change_id: str,
        *,
        add: list[str] | None = None,
        remove: list[int] | None = None,
        ccs: list[str] | None = None,
    ) -> Change:
        """Add and remove reviewers via REST, then cache fresh ChangeInfo."""

        rest_ref = change_id_for_gerrit_rest_path(change_id)
        cache_key = _cache_triplet(self._service, change_id)
        try:
            calls: list[Callable[[], Any]] = []
            if add or ccs:
                calls.append(self._set_reviewers_job(rest_ref, add or [], ccs or []))
            for account_id in remove or []:
                calls.append(self._delete_reviewer_job(rest_ref, account_id))
            parallel_map(calls)
            return self._service._refresh_after_mutation(change_id)
        except GerritApiError:
            self._service.cache.invalidate_changes([cache_key])
            raise

    def _set_reviewers_job(self, change_id: str, reviewers: list[str], ccs: list[str]) -> Callable[[], Any]:
        def _job() -> Any:
            return self._service.rest.set_reviewers_batch(change_id, reviewers=reviewers, ccs=ccs)

        return _job

    def _delete_reviewer_job(self, change_id: str, account_id: int) -> Callable[[], Any]:
        def _job() -> Any:
            return self._service.rest.delete_reviewer(change_id, account_id)

        return _job


class AccountApi:
    """Cache-aware AccountInfo operations."""

    def __init__(self, service: GerritService) -> None:
        self._service = service

    def get(self, account_id: int | str) -> Account:
        """Return one account."""

        payloads = self.get_payloads([account_id])
        payload = payloads.get(int(account_id))
        if payload is None:
            raise GerritApiError(f"no matching account {account_id}")
        return Account(payload)

    def get_payloads(self, account_ids: list[int | str]) -> dict[int, dict[str, Any]]:
        """Return raw AccountInfo payloads keyed by numeric account id."""

        return self._service.cache.load_accounts(
            account_ids,
            fetch_accounts=self._service._fetch_account_payloads,
            ttl_seconds=self._service.account_ttl_seconds,
            refresh=self._service.refresh,
        )


class CommentApi:
    """Cache-aware inline comment operations."""

    def __init__(self, service: GerritService) -> None:
        self._service = service

    def get_file_map(
        self,
        change_id: str,
        *,
        change_updated: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Return raw Gerrit comments grouped by file path."""

        return self._service.cache.load_comments(
            change_id,
            fetch_comments=self._service.rest.get_comments,
            change_updated=change_updated,
            trust_window_seconds=self._service.trust_window_seconds,
            refresh=self._service.refresh,
        )

    def get(self, change_id: str, *, change_updated: str | None = None) -> list[Comment]:
        """Return comments as object wrappers."""

        file_map = self.get_file_map(change_id, change_updated=change_updated)
        return [Comment(path=path, payload=payload) for path, rows in file_map.items() for payload in rows]


class ChecksApi:
    """Cache-aware CI checks and message operations."""

    def __init__(self, service: GerritService) -> None:
        self._service = service

    def get_rows(
        self,
        change_id: str,
        *,
        fetch_checks: Callable[[str], tuple[list[dict[str, Any]], list[dict[str, Any]]]] | None = None,
        change_updated: str | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Return Checks-plugin rows and change messages for one change."""

        def _default_fetch(triplet: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
            return (
                self._service._checks_or_empty(triplet),
                self._service._get_messages_or_empty(triplet),
            )

        return self._service.cache.load_checks(
            change_id,
            fetch_checks=fetch_checks or _default_fetch,
            change_updated=change_updated,
            trust_window_seconds=self._service.trust_window_seconds,
            refresh=self._service.refresh,
        )
