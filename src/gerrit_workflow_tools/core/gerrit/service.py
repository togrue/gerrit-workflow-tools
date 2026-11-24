"""High-level Gerrit API service with cache-aware batch operations."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.cache import DEFAULT_CHANGE_TRUST_WINDOW_SECONDS, GerritCache
from gerrit_workflow_tools.core.gerrit.change_view import ChangeView
from gerrit_workflow_tools.core.gerrit.changes import ChangeApi
from gerrit_workflow_tools.core.gerrit.comments import CommentApi
from gerrit_workflow_tools.core.gerrit.http_rest import HttpGerritRest, gerrit_auth_from_settings
from gerrit_workflow_tools.core.gerrit.rest import (
    GerritApiError,
    GerritRest,
    change_id_for_gerrit_rest_path,
    parallel_map,
    self_account_id as account_id_of_self,
)
from gerrit_workflow_tools.core.gerrit.stack_context import StackContext, build_triplet
from gerrit_workflow_tools.core.review_chain import ReviewChain, assemble_review_chains, missing_parent_shas


if TYPE_CHECKING:
    from gerrit_workflow_tools.core.repository import Repository


logger = logging.getLogger(__name__)

# Host capability key: does this Gerrit serve the Checks plugin endpoint?
_CHECKS_CAPABILITY = "checks"


class GerritService:
    """High-level object API layered on cached, parallel Gerrit REST access."""

    def __init__(
        self,
        rest: GerritRest | None = None,
        cache: GerritCache | None = None,
        *,
        trust_window_seconds: int = DEFAULT_CHANGE_TRUST_WINDOW_SECONDS,
        refresh: bool = False,
        repo: Repository,
    ) -> None:
        # Both are built on first use when not supplied: a service that is never asked
        # anything never needs ``ger.webUrl``, credentials or the cache file.
        self._rest = rest
        self._cache = cache
        # The repository of the run this service belongs to: its settings, its working
        # branch and the stack context on it, each read once for the run.
        self.repo = repo
        self.trust_window_seconds = trust_window_seconds
        self.refresh = refresh
        # Whether this host serves the Checks plugin: True / False / None when not yet
        # known. A property of the host, not of any change, so it is answered from the
        # cache and only rediscovered once that verdict expires.
        self._checks_endpoint_available: bool | None = None
        # Who you are, once asked in this run: an account id never changes.
        self._self_account_id: int | None = None
        self.changes = ChangeApi(self)
        self.comments = CommentApi(self)

    @property
    def settings(self) -> Settings:
        """The run's **Settings** snapshot."""
        return self.repo.settings

    @property
    def cwd(self) -> Path:
        """The repository this service reports on."""
        return self.repo.cwd

    @property
    def configured(self) -> bool:
        """Whether this service can reach a Gerrit: a client was supplied, or ``ger.webUrl`` is set."""
        return self._rest is not None or bool(self.settings.gerrit_web_url)

    @property
    def credentials_configured(self) -> bool:
        """Whether asking this Gerrit can authenticate: a client was supplied, or credentials are set."""
        return self._rest is not None or gerrit_auth_from_settings(self.settings) is not None

    @property
    def rest(self) -> GerritRest:
        """The Gerrit seam. Built from :attr:`settings` on first use when none was supplied.

        Raises :class:`ConfigError` then when ``ger.webUrl`` is unset, so only the work
        that genuinely asks Gerrit needs it configured.
        """
        if self._rest is None:
            self._rest = HttpGerritRest.from_settings(self.settings)
        return self._rest

    @property
    def cache(self) -> GerritCache:
        """The Gerrit cache for this host. Opened on first use when none was supplied."""
        if self._cache is None:
            self._cache = GerritCache.for_web_base(self.rest.web_base)
        return self._cache

    @property
    def web_base(self) -> str:
        """Configured Gerrit web base."""

        return self.rest.web_base

    def self_account_id(self) -> int | None:
        """The calling user's account id, or ``None`` when Gerrit will not say.

        Asked once per user and host and then remembered: an account id never changes, and
        every inbox load and review delta needs it.
        """
        if self._self_account_id is not None:
            return self._self_account_id
        user = self.settings.get("ger.user") or ""
        known = None if self.refresh else self.cache.account_id(user)
        if known is not None:
            self._self_account_id = known
            return known
        asked = account_id_of_self(self.rest)
        if asked is not None:
            self.cache.set_account_id(user, asked)
            self._self_account_id = asked
        return asked

    def stack_context(self) -> StackContext:
        """The stack context of the run's working branch (:attr:`Repository.stack`)."""
        return self.repo.stack

    def cache_key(self, change_ref: str, ctx: StackContext | None = None) -> str:
        """The one key a change is cached by and addressed by in every ``/changes/{id}`` request.

        Triplets, Gerrit ids and numbers verbatim; a bare Change-Id becomes the push branch's
        triplet, spelled with Gerrit's leading ``I`` even when given as ``i…``. A bare Change-Id is
        never a key: Gerrit answers it with 404 ("Multiple changes found") once the Change-Id is on
        a second branch, and the ``/changes/{id}`` seam refuses it (:func:`change_path_id`).
        Without a stack context (*ctx* or the run's) a bare Change-Id raises ``ChangeResolutionError``.
        """
        if "~" in change_ref or change_ref.isdigit():
            return change_ref
        ctx = ctx or self.stack_context()
        return build_triplet(ctx.project, ctx.push_branch, change_id_for_gerrit_rest_path(change_ref))

    def fetch_gerrit_data(
        self,
        commits: list[Any],
        *,
        cwd: Path | str | None = None,
        fetch_ci_pipelines: bool = False,
        triplets: Sequence[str | None] | None = None,
    ) -> list[Any]:
        """Return ``LogCommit`` rows enriched with cached Gerrit data and parallel follow-ups.

        Each row is overlaid with the push branch's change carrying its Change-Id, unless
        *triplets* (one per row) names the change for it: a change shown for itself.
        """

        from gerrit_workflow_tools.core.gerrit_change_status import build_log_commit, overlay_code_facts

        stack = self.stack_context()
        keys = [
            named
            or (build_triplet(stack.project, stack.push_branch, row.valid_change_id) if row.valid_change_id else None)
            for row, named in zip(commits, triplets or [None] * len(commits), strict=True)
        ]
        refs = [key for key in keys if key]
        detail_map = self.changes.get_payloads(refs) if refs else {}
        by_triplet: dict[str, ChangeView] = {}
        for ref, payload in detail_map.items():
            view = ChangeView.from_payload(payload)
            by_triplet[ref] = view
            if view.key:
                by_triplet[view.key] = view

        changes = [by_triplet.get(key) if key else None for key in keys]
        facts = overlay_code_facts(cwd, zip(commits, changes, strict=True))
        result: list[Any] = []
        follow_ups: list[Callable[[], None]] = []
        for row, change in zip(commits, changes, strict=True):
            lc, needed = build_log_commit(row, change, self.web_base, facts)
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
        must not call :func:`resolve_stack_context`. Gerrit is always asked what matches
        (:meth:`ChangeApi.query`: a light listing, details from the cache where current),
        then once more, batched, for parents missing from the result set.
        """
        moment = now if now is not None else datetime.now(timezone.utc)
        rows = [ChangeView.from_payload(row) for row in self.changes.query(query)]
        account_id = self_account_id if self_account_id is not None else self.self_account_id()
        parent_shas = missing_parent_shas(rows)
        follow_up_rows: list[ChangeView] = []
        unmatched: set[str] = set()
        if parent_shas:
            follow_up_rows = [ChangeView.from_payload(payload) for payload in self.changes.by_commit(parent_shas)]
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
                self.repo.root,
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
