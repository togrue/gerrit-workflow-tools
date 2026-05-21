"""High-level Gerrit API service with cache-aware batch operations."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from gerrit_workflow_tools.core.gerrit.cache import (
    DEFAULT_ACCOUNT_TTL_SECONDS,
    DEFAULT_CHANGE_TRUST_WINDOW_SECONDS,
    GerritCache,
)
from gerrit_workflow_tools.core.gerrit.models import Account, Change, Comment
from gerrit_workflow_tools.core.gerrit.rest import (
    GerritApiError,
    GerritClient,
    change_id_for_gerrit_rest_path,
    parallel_map,
    resolve_gerrit_web_base,
)


def _change_key(change_id: str) -> str:
    return change_id_for_gerrit_rest_path(change_id)


def _canonical_change_map(rows: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for fallback, payload in rows.items():
        raw = payload.get("change_id")
        key = _change_key(raw if isinstance(raw, str) else fallback)
        out[key] = payload
    return out


class GerritService:
    """High-level object API layered on cached, parallel Gerrit REST access."""

    def __init__(
        self,
        rest: GerritClient,
        cache: GerritCache,
        *,
        trust_window_seconds: int = DEFAULT_CHANGE_TRUST_WINDOW_SECONDS,
        account_ttl_seconds: int = DEFAULT_ACCOUNT_TTL_SECONDS,
        refresh: bool = False,
    ) -> None:
        self.rest = rest
        self.cache = cache
        self.trust_window_seconds = trust_window_seconds
        self.account_ttl_seconds = account_ttl_seconds
        self.refresh = refresh
        self.changes = ChangeApi(self)
        self.accounts = AccountApi(self)
        self.comments = CommentApi(self)

    @classmethod
    def from_cwd(
        cls,
        cwd: Path | str | None,
        *,
        refresh: bool = False,
        trust_window_seconds: int = DEFAULT_CHANGE_TRUST_WINDOW_SECONDS,
    ) -> GerritService:
        """Construct a service from git-configured Gerrit settings."""

        if os.environ.get("GER_CACHE_REFRESH", "").strip().lower() in ("1", "true", "yes"):
            refresh = True
        web_base = resolve_gerrit_web_base(cwd)
        rest = GerritClient(web_base, cwd=str(cwd) if cwd is not None else None)
        cache = GerritCache.for_web_base(web_base)
        return cls(rest, cache, refresh=refresh, trust_window_seconds=trust_window_seconds)

    @property
    def web_base(self) -> str:
        """Configured Gerrit web base."""

        return self.rest.web_base

    def _fetch_change_payloads(self, change_ids: list[str]) -> dict[str, dict[str, Any]]:
        from gerrit_workflow_tools.core.gerrit_change_status import batch_load_change_details

        return _canonical_change_map(batch_load_change_details(self.rest, change_ids))

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

    def fetch_gerrit_data(self, commits: list[Any], *, cwd: Path | str | None = None) -> list[Any]:
        """Return ``LogCommit`` rows using cached ChangeInfo and cached comments."""

        from gerrit_workflow_tools.core.gerrit_change_status import fetch_gerrit_data, norm_change_id

        ids = [row.change_id for row in commits if row.change_id]
        details = self.changes.get_payloads(ids)
        normalized_details: dict[str, dict[str, Any]] = {}
        for key, payload in details.items():
            normalized_details[norm_change_id(key)] = payload
        return fetch_gerrit_data(
            self.rest,
            self.web_base,
            commits,
            cwd=cwd,
            detail_cache=normalized_details,
            comments_fetcher=self.comments.get_file_map,
            change_fetcher=lambda change_id: self.changes.get(change_id).payload,
        )

    def _refresh_after_mutation(self, change_id: str) -> Change:
        key = _change_key(change_id)
        try:
            payload = self.rest.get_change(key)
        except GerritApiError:
            self.cache.invalidate_changes([key])
            raise
        self.cache.invalidate_changes([key])
        self.cache.upsert_changes({key: payload})
        return Change(payload)


class ChangeApi:
    """Cache-aware ChangeInfo operations."""

    def __init__(self, service: GerritService) -> None:
        self._service = service

    def get_many(self, change_ids: list[str]) -> list[Change]:
        """Return changes in the same order as *change_ids*."""

        payloads = self.get_payloads(change_ids)
        out: list[Change] = []
        for cid in change_ids:
            payload = payloads.get(_change_key(cid))
            if payload is not None:
                out.append(Change(payload))
        return out

    def get(self, change_id: str) -> Change:
        """Return one change or raise if Gerrit did not return it."""

        payloads = self.get_payloads([change_id])
        payload = payloads.get(_change_key(change_id))
        if payload is None:
            raise GerritApiError(f"no matching change {change_id}")
        return Change(payload)

    def get_payloads(self, change_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Return raw ChangeInfo payloads keyed by canonical Change-Id."""

        return self._service.cache.load_changes(
            change_ids,
            probe_updated=self._service.rest.probe_changes_updated,
            fetch_changes=self._service._fetch_change_payloads,
            trust_window_seconds=self._service.trust_window_seconds,
            refresh=self._service.refresh,
        )

    def set_topic(self, change_id: str, topic: str | None) -> Change:
        """Set or clear the change topic via REST and update cache."""

        key = _change_key(change_id)
        try:
            self._service.rest.set_topic(key, topic)
            return self._service._refresh_after_mutation(key)
        except GerritApiError:
            self._service.cache.invalidate_changes([key])
            raise

    def set_wip(self, change_id: str, on: bool) -> Change:
        """Set or clear work-in-progress state via REST and update cache."""

        key = _change_key(change_id)
        try:
            self._service.rest.set_wip(key, on)
            return self._service._refresh_after_mutation(key)
        except GerritApiError:
            self._service.cache.invalidate_changes([key])
            raise

    def set_private(self, change_id: str, on: bool) -> Change:
        """Set or clear private state via REST and update cache."""

        key = _change_key(change_id)
        try:
            self._service.rest.set_private(key, on)
            return self._service._refresh_after_mutation(key)
        except GerritApiError:
            self._service.cache.invalidate_changes([key])
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

        key = _change_key(change_id)
        try:
            calls: list[Callable[[], Any]] = []
            if add or ccs:
                calls.append(self._set_reviewers_job(key, add or [], ccs or []))
            for account_id in remove or []:
                calls.append(self._delete_reviewer_job(key, account_id))
            parallel_map(calls)
            return self._service._refresh_after_mutation(key)
        except GerritApiError:
            self._service.cache.invalidate_changes([key])
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

    def get_many(self, account_ids: list[int | str]) -> list[Account]:
        """Return accounts in input order when present."""

        payloads = self.get_payloads(account_ids)
        out: list[Account] = []
        for account_id in account_ids:
            payload = payloads.get(int(account_id))
            if payload is not None:
                out.append(Account(payload))
        return out

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
