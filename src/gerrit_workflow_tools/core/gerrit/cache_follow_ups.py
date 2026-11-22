"""Follow-ups of a change in the Gerrit cache: its comments and its Checks rows.

A follow-up is served only while it is provably current: while the change's freshness token
(``change_freshness_key``, ADR-0007) is the one it was stored under, or, without a token,
inside the trust window. :class:`~gerrit_workflow_tools.core.gerrit.cache.GerritCache` mixes
this in and brings the database connection and the clock.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Any, TypeVar

from gerrit_workflow_tools.core.gerrit.cache_rows import decode_checks, decode_comments, json_dumps
from gerrit_workflow_tools.core.gerrit.cache_schema import placeholders


# How long a cached row is served without asking Gerrit (the **trust window**).
DEFAULT_CHANGE_TRUST_WINDOW_SECONDS = 10

# Follow-up tables and the columns that key one row.
FOLLOW_UP_KEYS = {"comments": ("change_id", "context_key"), "checks": ("change_id",)}

_T = TypeVar("_T")


class FollowUpCache:
    """Comments and Checks rows, keyed by change and validated by its freshness token."""

    def _connect(self) -> sqlite3.Connection:
        raise NotImplementedError

    def _clock(self) -> int:
        raise NotImplementedError

    def _load_follow_up(
        self,
        table: str,
        key: tuple[str, ...],
        *,
        decode: Callable[[Any], _T | None],
        fetch: Callable[[], _T],
        store: Callable[[_T], None],
        change_updated: str | None,
        trust_window_seconds: int,
        refresh: bool,
    ) -> _T:
        """Serve one follow-up row only while it is provably current, else fetch and store it."""
        where = " AND ".join(f"{column} = ?" for column in FOLLOW_UP_KEYS[table])
        with self._connect() as conn:
            raw = conn.execute(f"SELECT payload, fetched_at, change_updated FROM {table} WHERE {where}", key).fetchone()
        if raw and not refresh:
            cached = decode(json.loads(str(raw["payload"])))
            # The freshness token decides when there is one; otherwise only the trust window can.
            if change_updated is not None:
                current = raw["change_updated"] == change_updated
            else:
                current = 0 <= self._clock() - int(raw["fetched_at"]) < trust_window_seconds
            if cached is not None and current:
                return cached
        fresh = fetch()
        store(fresh)
        return fresh

    def _store_follow_up(self, table: str, key: tuple[str, ...], payload: Any, change_updated: str | None) -> None:
        columns = (*FOLLOW_UP_KEYS[table], "payload", "fetched_at", "change_updated")
        with self._connect() as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO {table}({','.join(columns)}) VALUES ({placeholders(columns)})",
                (*key, json_dumps(payload), self._clock(), change_updated),
            )

    def load_comments(
        self,
        triplet: str,
        *,
        fetch_comments: Callable[[str], dict[str, list[dict[str, Any]]]],
        change_updated: str | None = None,
        trust_window_seconds: int = DEFAULT_CHANGE_TRUST_WINDOW_SECONDS,
        refresh: bool = False,
        context_key: str = "",
    ) -> dict[str, list[dict[str, Any]]]:
        """Load comment payloads keyed by the change freshness token and context variant.

        When *change_updated* is supplied, it is the validity key (``meta_rev_id`` or
        the ``updated``+counts fallback). A changed key refetches even inside the trust
        window. Without a key, the trust window alone applies.

        *context_key* distinguishes ``GET …/comments`` from the same path with
        ``enable-context`` / ``context-padding``. Those payloads must not share a row.
        """

        return self._load_follow_up(
            "comments",
            (triplet, context_key),
            decode=decode_comments,
            fetch=lambda: fetch_comments(triplet),
            store=lambda payload: self.upsert_comments(
                triplet, payload, change_updated=change_updated, context_key=context_key
            ),
            change_updated=change_updated,
            trust_window_seconds=trust_window_seconds,
            refresh=refresh,
        )

    def upsert_comments(
        self,
        triplet: str,
        payload: dict[str, list[dict[str, Any]]],
        *,
        change_updated: str | None = None,
        context_key: str = "",
    ) -> None:
        """Store comments for one change and context variant."""

        self._store_follow_up("comments", (triplet, context_key), payload, change_updated)

    def load_checks(
        self,
        triplet: str,
        *,
        fetch_checks: Callable[[str], tuple[list[dict[str, Any]], list[dict[str, Any]]]],
        change_updated: str | None = None,
        trust_window_seconds: int = DEFAULT_CHANGE_TRUST_WINDOW_SECONDS,
        refresh: bool = False,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Load Checks-plugin rows and messages keyed by the change freshness token."""

        return self._load_follow_up(
            "checks",
            (triplet,),
            decode=decode_checks,
            fetch=lambda: fetch_checks(triplet),
            store=lambda rows: self.upsert_checks(triplet, *rows, change_updated=change_updated),
            change_updated=change_updated,
            trust_window_seconds=trust_window_seconds,
            refresh=refresh,
        )

    def upsert_checks(
        self,
        triplet: str,
        checks: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        *,
        change_updated: str | None = None,
    ) -> None:
        """Store checks and messages for one change."""

        self._store_follow_up("checks", (triplet,), {"checks": checks, "messages": messages}, change_updated)
