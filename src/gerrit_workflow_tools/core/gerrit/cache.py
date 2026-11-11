"""SQLite-backed Gerrit API cache."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gerrit_workflow_tools.core.gerrit.cache_facts import FACT_PREFIXES, RememberedFacts
from gerrit_workflow_tools.core.gerrit.cache_follow_ups import (
    DEFAULT_CHANGE_TRUST_WINDOW_SECONDS,
    FOLLOW_UP_KEYS,
    FollowUpCache,
)
from gerrit_workflow_tools.core.gerrit.cache_rows import (
    CacheInfo,
    dict_rows,
    footer_of_triplet,
    json_dumps,
    payload_number,
    payload_triplet,
    payload_updated,
    strip_avatars,
)
from gerrit_workflow_tools.core.gerrit.cache_schema import (
    DATA_TABLES,
    SCHEMA_VERSION,
    create_tables,
    drop_tables,
    meta_get,
    meta_set,
    placeholders,
)
from gerrit_workflow_tools.core.gerrit.change_queries import alias_batch_fetch_results, change_freshness_key
from gerrit_workflow_tools.core.gerrit.paths import gerrit_cache_db_path, gerrit_cache_host


@dataclass(frozen=True)
class _ChangeRow:
    payload: dict[str, Any]
    verified_at: int


def _now() -> int:
    return int(time.time())


# ``meta`` keys holding Gerrit's answer to "which changes carry this Change-Id".
_LOOKUP_PREFIX = "lookup:"
# An answer this old is of no use to anyone and is dropped with the next one stored.
_LOOKUP_KEPT_SECONDS = 60 * 60


def _within(checked_at: int, seconds: int) -> bool:
    """Whether *checked_at* is at most *seconds* ago. A time in the future (the clock was set back) is not."""
    return 0 <= _now() - checked_at < seconds


class GerritCache(FollowUpCache, RememberedFacts):
    """Shared SQLite cache for one Gerrit remote."""

    def __init__(self, path: Path, *, web_base: str, host: str | None = None) -> None:
        self.path = path
        self.web_base = web_base.rstrip("/")
        self.host = host or gerrit_cache_host(web_base)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @classmethod
    def for_web_base(cls, web_base: str) -> GerritCache:
        """Open the cache DB for *web_base*."""

        return cls(gerrit_cache_db_path(web_base), web_base=web_base)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=2000")
        return conn

    def _clock(self) -> int:
        return _now()

    def _init_db(self) -> None:
        with self._connect() as conn:
            create_tables(conn)
            version = meta_get(conn, "schema_version")
            if version and version != SCHEMA_VERSION:
                drop_tables(conn)
            create_tables(conn)
            meta_set(conn, "schema_version", SCHEMA_VERSION)
            meta_set(conn, "host", self.host)
            meta_set(conn, "web_base", self.web_base)

    def clear(self) -> None:
        """Delete all cached API payloads while keeping schema metadata."""
        with self._connect() as conn:
            for table in DATA_TABLES:
                conn.execute(f"DELETE FROM {table}")
            for prefix in (*FACT_PREFIXES, _LOOKUP_PREFIX):
                conn.execute("DELETE FROM meta WHERE key LIKE ?", (f"{prefix}%",))

    def info(self) -> CacheInfo:
        """Return row counts for cache administration."""

        with self._connect() as conn:
            counts = {
                table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in ("changes", "comments", "checks")
            }
        return CacheInfo(path=self.path, host=self.host, **counts)

    def _lookup_changes(self, triplets: list[str]) -> dict[str, _ChangeRow]:
        if not triplets:
            return {}
        out: dict[str, _ChangeRow] = {}
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT change_id, payload, fetched_at
                FROM changes WHERE change_id IN ({placeholders(triplets)})
                """,
                triplets,
            ).fetchall()
        for row in rows:
            payload = json.loads(str(row["payload"]))
            if isinstance(payload, dict):
                out[str(row["change_id"])] = _ChangeRow(payload=payload, verified_at=int(row["fetched_at"]))
        return out

    def _mark_verified(self, conn: sqlite3.Connection, triplets: list[str]) -> None:
        """Gerrit just confirmed these rows are current: their trust window starts again."""
        if triplets:
            conn.execute(
                f"UPDATE changes SET fetched_at = ? WHERE change_id IN ({placeholders(triplets)})",
                [_now(), *triplets],
            )

    def _missing_is_fresh(self, conn: sqlite3.Connection, footer_id: str, *, ttl_seconds: int) -> bool:
        row = conn.execute(
            "SELECT checked_at FROM missing_changes WHERE change_id = ?",
            (footer_id,),
        ).fetchone()
        return row is not None and _within(int(row["checked_at"]), ttl_seconds)

    def _mark_missing(self, conn: sqlite3.Connection, footer_id: str) -> None:
        conn.execute(
            "INSERT OR REPLACE INTO missing_changes(change_id, checked_at) VALUES (?, ?)",
            (footer_id, _now()),
        )

    def _clear_missing(self, conn: sqlite3.Connection, footer_id: str) -> None:
        conn.execute("DELETE FROM missing_changes WHERE change_id = ?", (footer_id,))

    def trusted_change(self, key: str, *, trust_window_seconds: int) -> dict[str, Any] | None:
        """The payload cached under *key* while it is inside its trust window, else ``None``."""
        row = self._lookup_changes([key]).get(key)
        return row.payload if row is not None and _within(row.verified_at, trust_window_seconds) else None

    def carrying(self, change_id: str, *, trust_window_seconds: int) -> list[dict[str, Any]] | None:
        """Gerrit's last answer to "which changes carry *change_id*", while it is inside the trust window."""
        with self._connect() as conn:
            raw = meta_get(conn, _LOOKUP_PREFIX + change_id.lower())
        stored = json.loads(raw) if raw else None
        if not isinstance(stored, dict) or not _within(int(stored.get("at", 0)), trust_window_seconds):
            return None
        return dict_rows(stored.get("rows"))

    def remember_carrying(self, change_id: str, rows: list[dict[str, Any]]) -> None:
        """Keep Gerrit's answer to "which changes carry *change_id*" (ChangeInfo rows, possibly none)."""
        now = _now()
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM meta WHERE key LIKE ? AND json_extract(value, '$.at') < ?",
                (f"{_LOOKUP_PREFIX}%", now - _LOOKUP_KEPT_SECONDS),
            )
            answer = {"at": now, "rows": [strip_avatars(row) for row in rows]}
            meta_set(conn, _LOOKUP_PREFIX + change_id.lower(), json_dumps(answer))

    def _forget_carrying(self, conn: sqlite3.Connection, change_id: str) -> None:
        conn.execute("DELETE FROM meta WHERE key = ?", (_LOOKUP_PREFIX + change_id.lower(),))

    def find_payloads_by_footer_change_ids(self, change_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        """Return cached ChangeInfo rows grouped by footer ``change_id`` (local only)."""
        unique = list(dict.fromkeys(cid for cid in change_ids if cid))
        if not unique:
            return {}
        out: dict[str, list[dict[str, Any]]] = {cid: [] for cid in unique}
        seen: dict[str, set[tuple[Any, Any]]] = {cid: set() for cid in unique}
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT payload FROM changes
                WHERE json_extract(payload, '$.change_id') IN ({placeholders(unique)})
                """,
                unique,
            ).fetchall()
        for row in rows:
            payload = json.loads(str(row["payload"]))
            if not isinstance(payload, dict):
                continue
            cid = payload.get("change_id")
            if not isinstance(cid, str) or cid not in out:
                continue
            key = (payload.get("_number"), payload.get("branch"))
            if key in seen[cid]:
                continue
            seen[cid].add(key)
            out[cid].append(payload)
        return out

    def load_changes(
        self,
        triplets: list[str],
        *,
        probe_updated: Callable[[list[str]], dict[str, str]],
        fetch_changes: Callable[[list[str]], dict[str, dict[str, Any]]],
        trust_window_seconds: int = DEFAULT_CHANGE_TRUST_WINDOW_SECONDS,
        refresh: bool = False,
    ) -> dict[str, dict[str, Any]]:
        """Load ChangeInfo payloads keyed by Gerrit triplet ``id``.

        A row inside its trust window is served as it is. Past it, one probe asks Gerrit for
        the freshness tokens of those rows: a row whose token did not move is served and its
        trust window starts again, the others are fetched. So an unchanged stack costs one
        light request, and nothing at all for the next *trust_window_seconds*.

        "Gerrit has no such change" is trusted for the same window and no longer, so a change
        pushed from elsewhere (plain ``git push``, another clone) shows as soon as a changed
        one would.

        The probe also names the same Change-Ids on other branches (**siblings**). One that is
        new or moved is fetched too, so what the cache says about siblings is as current as
        the stack, not as old as the last time a stack change happened to move.

        Database connections are never held across Gerrit network calls.
        """
        rows = self._lookup_changes(triplets)
        out: dict[str, dict[str, Any]] = {}
        probe_ids: list[str] = []
        fetch_ids: list[str] = []
        any_cached = any(triplet in rows for triplet in triplets)

        with self._connect() as conn:
            for triplet in triplets:
                row = rows.get(triplet)
                if row is None:
                    if (
                        not refresh
                        and not any_cached
                        and self._missing_is_fresh(conn, footer_of_triplet(triplet), ttl_seconds=trust_window_seconds)
                    ):
                        continue
                    fetch_ids.append(triplet)
                elif not refresh and _within(row.verified_at, trust_window_seconds):
                    out[triplet] = row.payload
                else:
                    probe_ids.append(triplet)

        siblings: list[str] = []
        if probe_ids:
            tokens = probe_updated(probe_ids)
            # Gerrit answers under its own id, which is not always the key that was asked for.
            asked = {key: tokens.get(key) or tokens.get(payload_triplet(rows[key].payload) or "") for key in probe_ids}
            current, moved = self.reconcile(asked)
            out.update(current)
            fetch_ids.extend(moved)
            own = {*asked, *(payload_triplet(rows[key].payload) for key in probe_ids)}
            siblings = self.reconcile({key: token for key, token in tokens.items() if key not in own})[1]

        if fetch_ids or siblings:
            aliased = alias_batch_fetch_results(fetch_ids, fetch_changes([*fetch_ids, *siblings]))
            for ref in fetch_ids:
                if ref in aliased:
                    out[ref] = aliased[ref]
            with self._connect() as conn:
                self.upsert_changes(aliased, conn=conn)
                for ref in fetch_ids:
                    footer = footer_of_triplet(ref)
                    if ref in aliased:
                        self._clear_missing(conn, footer)
                    else:
                        self._mark_missing(conn, footer)

        return out

    def reconcile(self, tokens: Mapping[str, str | None]) -> tuple[dict[str, dict[str, Any]], list[str]]:
        """Split cache keys by whether the cached row still carries Gerrit's freshness token.

        *tokens* is what Gerrit just said: key to :func:`change_freshness_key`. Returns the
        payloads of the rows that are current, whose trust window starts again, and the keys
        to fetch: rows that moved, and rows the cache does not have.
        """
        rows = self._lookup_changes(list(tokens))
        current: dict[str, dict[str, Any]] = {}
        moved: list[str] = []
        for key, token in tokens.items():
            row = rows.get(key)
            if row is not None and token and token == change_freshness_key(row.payload):
                current[key] = row.payload
            else:
                moved.append(key)
        if current:
            with self._connect() as conn:
                self._mark_verified(conn, list(current))
        return current, moved

    def upsert_changes(
        self,
        changes: dict[str, dict[str, Any]] | list[dict[str, Any]],
        *,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        """Store ChangeInfo payloads under lookup keys and ``payload["id"]``."""

        keyed: dict[str | None, dict[str, Any]] = {}
        pairs = changes.items() if isinstance(changes, dict) else ((None, payload) for payload in changes)
        for cache_key, payload in pairs:
            if isinstance(payload, dict):
                keyed[cache_key] = payload
                keyed[payload_triplet(payload)] = payload
        keyed.pop(None, None)

        now = _now()
        stored = [(key, strip_avatars(payload)) for key, payload in keyed.items()]
        rows = [
            (key, payload_number(payload), json_dumps(payload), payload_updated(payload), now)
            for key, payload in stored
        ]
        insert = """
            INSERT OR REPLACE INTO changes(change_id, number, payload, updated, fetched_at)
            VALUES (?, ?, ?, ?, ?)
        """
        if conn is not None:
            conn.executemany(insert, rows)
        else:
            with self._connect() as connection:
                connection.executemany(insert, rows)

    def invalidate_changes(self, triplets: list[str]) -> None:
        """Drop cached change, comment, checks and "no such change" rows for *triplets*.

        A change is stored under the key it was asked for and under Gerrit's own id. Both go,
        and so does what was remembered about who carries its Change-Id, so no copy of it
        outlives the write that made it stale.
        """
        if not triplets:
            return
        with self._connect() as conn:
            aliases = conn.execute(
                f"""
                SELECT change_id FROM changes WHERE number IN (
                  SELECT number FROM changes WHERE number IS NOT NULL AND change_id IN ({placeholders(triplets)})
                )
                """,
                triplets,
            ).fetchall()
            keys = list(dict.fromkeys([*triplets, *(str(row[0]) for row in aliases)]))
            for table in ("changes", *FOLLOW_UP_KEYS):
                conn.execute(f"DELETE FROM {table} WHERE change_id IN ({placeholders(keys)})", keys)
            for triplet in triplets:
                self._clear_missing(conn, footer_of_triplet(triplet))
            for key in keys:
                self._forget_carrying(conn, footer_of_triplet(key))

    def forget_change(self, number: int, change_id: str | None = None) -> bool:
        """Drop every row cached for change *number*, under any key, and a "no such change" for *change_id*.

        For news that the change moved (an event): it only ever narrows what is trusted.
        Returns whether anything was cached.
        """
        with self._connect() as conn:
            keys = [str(row[0]) for row in conn.execute("SELECT change_id FROM changes WHERE number = ?", (number,))]
            cleared = conn.execute("DELETE FROM missing_changes WHERE change_id = ?", (change_id or "",)).rowcount
            self._forget_carrying(conn, change_id or "")
        self.invalidate_changes(keys)
        return bool(keys) or cleared > 0
