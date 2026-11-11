"""SQLite-backed Gerrit API cache."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable, Sized
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from gerrit_workflow_tools.core.gerrit.cache_rows import (
    decode_checks,
    decode_comments,
    footer_of_triplet,
    max_updated,
    payload_number,
    payload_triplet,
    payload_updated,
    strip_avatars,
)
from gerrit_workflow_tools.core.gerrit.change_queries import alias_batch_fetch_results, change_freshness_key
from gerrit_workflow_tools.core.gerrit.paths import gerrit_cache_db_path, gerrit_cache_host


SCHEMA_VERSION = "4"
DEFAULT_CHANGE_TRUST_WINDOW_SECONDS = 10
DEFAULT_CAPABILITY_TTL_SECONDS = 7 * 24 * 60 * 60
DEFAULT_MISSING_CHANGE_TTL_SECONDS = 180

_CAPABILITY_PREFIX = "capability:"

_DATA_TABLES = ("checks", "comments", "changes", "missing_changes", "scope_state")
# Follow-up tables and the columns that key one row.
_FOLLOW_UP_KEYS = {"comments": ("change_id", "context_key"), "checks": ("change_id",)}

_T = TypeVar("_T")


def comments_context_key(context_padding: int | None) -> str:
    """Cache discriminator for comment payloads with or without source context."""

    if context_padding is None:
        return ""
    return f"p{context_padding}"


@dataclass(frozen=True)
class CacheInfo:
    """Small summary for cache administration commands."""

    path: Path
    host: str
    changes: int
    comments: int
    checks: int


@dataclass(frozen=True)
class _ChangeRow:
    payload: dict[str, Any]
    updated: str | None
    fetched_at: int
    certified_gen: int | None


@dataclass(frozen=True)
class _ScopeState:
    watermark: str
    generation: int


def _now() -> int:
    return int(time.time())


def _placeholders(values: Sized) -> str:
    return ",".join("?" * len(values))


def _json_dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


class GerritCache:
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

    def _init_db(self) -> None:
        with self._connect() as conn:
            self._create_tables(conn)
            version = self._meta_get(conn, "schema_version")
            if version and version != SCHEMA_VERSION:
                self._drop_tables(conn)
            self._create_tables(conn)
            self._meta_set(conn, "schema_version", SCHEMA_VERSION)
            self._meta_set(conn, "host", self.host)
            self._meta_set(conn, "web_base", self.web_base)

    @staticmethod
    def _drop_tables(conn: sqlite3.Connection) -> None:
        for table in (*_DATA_TABLES, "meta", "accounts"):  # accounts is retired; dropped on schema reset
            conn.execute(f"DROP TABLE IF EXISTS {table}")

    @staticmethod
    def _create_tables(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS changes (
              change_id      TEXT PRIMARY KEY,
              number         INTEGER,
              payload        TEXT NOT NULL,
              updated        TEXT,
              fetched_at     INTEGER NOT NULL,
              certified_gen  INTEGER
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS changes_number ON changes(number)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS changes_footer_change_id ON changes(json_extract(payload, '$.change_id'))"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scope_state (
              scope_key   TEXT PRIMARY KEY,
              watermark   TEXT NOT NULL,
              generation  INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS missing_changes (
              change_id   TEXT PRIMARY KEY,
              checked_at  INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS comments (
              change_id      TEXT NOT NULL,
              context_key    TEXT NOT NULL DEFAULT '',
              payload        TEXT NOT NULL,
              fetched_at     INTEGER NOT NULL,
              change_updated TEXT,
              PRIMARY KEY (change_id, context_key)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS checks (
              change_id      TEXT PRIMARY KEY,
              payload        TEXT NOT NULL,
              fetched_at     INTEGER NOT NULL,
              change_updated TEXT
            )
            """
        )

    @staticmethod
    def _meta_get(conn: sqlite3.Connection, key: str) -> str | None:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else None

    @staticmethod
    def _meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))

    def clear(self) -> None:
        """Delete all cached API payloads while keeping schema metadata."""
        with self._connect() as conn:
            for table in _DATA_TABLES:
                conn.execute(f"DELETE FROM {table}")
            conn.execute("DELETE FROM meta WHERE key LIKE ?", (f"{_CAPABILITY_PREFIX}%",))

    def capability(self, name: str, *, ttl_seconds: int = DEFAULT_CAPABILITY_TTL_SECONDS) -> bool | None:
        """Whether this host supports *name*, or ``None`` when unknown or stale."""

        with self._connect() as conn:
            raw = self._meta_get(conn, f"{_CAPABILITY_PREFIX}{name}")
        if not raw:
            return None
        try:
            record = json.loads(raw)
            value = record["value"]
            checked_at = int(record["at"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None
        if not isinstance(value, bool) or _now() - checked_at >= ttl_seconds:
            return None
        return value

    def set_capability(self, name: str, value: bool) -> None:
        """Record whether this host supports *name*."""

        with self._connect() as conn:
            self._meta_set(conn, f"{_CAPABILITY_PREFIX}{name}", _json_dumps({"value": value, "at": _now()}))

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
                SELECT change_id, payload, updated, fetched_at, certified_gen
                FROM changes WHERE change_id IN ({_placeholders(triplets)})
                """,
                triplets,
            ).fetchall()
        for row in rows:
            payload = json.loads(str(row["payload"]))
            if isinstance(payload, dict):
                certified = row["certified_gen"]
                out[str(row["change_id"])] = _ChangeRow(
                    payload=payload,
                    updated=row["updated"] if isinstance(row["updated"], str) else None,
                    fetched_at=int(row["fetched_at"]),
                    certified_gen=int(certified) if certified is not None else None,
                )
        return out

    def _get_scope_state(self, conn: sqlite3.Connection, scope_key: str) -> _ScopeState | None:
        row = conn.execute(
            "SELECT watermark, generation FROM scope_state WHERE scope_key = ?",
            (scope_key,),
        ).fetchone()
        if row is None:
            return None
        return _ScopeState(watermark=str(row["watermark"]), generation=int(row["generation"]))

    def _set_scope_state(self, conn: sqlite3.Connection, scope_key: str, watermark: str, generation: int) -> None:
        conn.execute(
            """
            INSERT OR REPLACE INTO scope_state(scope_key, watermark, generation)
            VALUES (?, ?, ?)
            """,
            (scope_key, watermark, generation),
        )

    def _stamp_certified_gen(self, conn: sqlite3.Connection, triplets: list[str], generation: int) -> None:
        if not triplets:
            return
        conn.execute(
            f"UPDATE changes SET certified_gen = ? WHERE change_id IN ({_placeholders(triplets)})",
            [generation, *triplets],
        )

    def _missing_is_fresh(self, conn: sqlite3.Connection, footer_id: str, *, ttl_seconds: int) -> bool:
        row = conn.execute(
            "SELECT checked_at FROM missing_changes WHERE change_id = ?",
            (footer_id,),
        ).fetchone()
        return row is not None and _now() - int(row["checked_at"]) < ttl_seconds

    def _mark_missing(self, conn: sqlite3.Connection, footer_id: str) -> None:
        conn.execute(
            "INSERT OR REPLACE INTO missing_changes(change_id, checked_at) VALUES (?, ?)",
            (footer_id, _now()),
        )

    def _clear_missing(self, conn: sqlite3.Connection, footer_id: str) -> None:
        conn.execute("DELETE FROM missing_changes WHERE change_id = ?", (footer_id,))

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
                WHERE json_extract(payload, '$.change_id') IN ({_placeholders(unique)})
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
        fetch_delta: Callable[[str], tuple[list[dict[str, Any]], bool]] | None = None,
        scope_key: str | None = None,
        trust_window_seconds: int = DEFAULT_CHANGE_TRUST_WINDOW_SECONDS,
        missing_ttl_seconds: int = DEFAULT_MISSING_CHANGE_TTL_SECONDS,
        refresh: bool = False,
    ) -> dict[str, dict[str, Any]]:
        """Load ChangeInfo payloads keyed by Gerrit triplet ``id``.

        Database connections are never held across Gerrit network calls.
        """
        rows = self._lookup_changes(triplets)
        now = _now()
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
                        and self._missing_is_fresh(conn, footer_of_triplet(triplet), ttl_seconds=missing_ttl_seconds)
                    ):
                        continue
                    fetch_ids.append(triplet)
                elif not refresh and now - row.fetched_at < trust_window_seconds:
                    out[triplet] = row.payload
                else:
                    probe_ids.append(triplet)

        # ``since:`` only sees ``ChangeInfo.updated``. A comment resolve in the same
        # second leaves that timestamp unchanged, so the delta is empty (or names
        # some other change). Those requested ids still need the meta_rev_id probe.
        if fetch_delta is not None and scope_key is not None and not refresh and not fetch_ids and probe_ids:
            covered = self._try_delta(triplets, rows, fetch_delta, scope_key, out)
            if covered is not None:
                probe_ids = [triplet for triplet in probe_ids if triplet not in covered]
                if not probe_ids:
                    return out

        did_network = False
        if probe_ids:
            did_network = True
            updated_by_id = probe_updated(probe_ids)
            for triplet in probe_ids:
                row = rows[triplet]
                remote_token = updated_by_id.get(triplet)
                if remote_token is None:
                    payload_id = row.payload.get("id")
                    if isinstance(payload_id, str):
                        remote_token = updated_by_id.get(payload_id)
                cached_key = change_freshness_key(row.payload)
                if cached_key and remote_token == cached_key:
                    out[triplet] = row.payload
                else:
                    fetch_ids.append(triplet)

        aliased: dict[str, dict[str, Any]] = {}
        if fetch_ids:
            did_network = True
            fetched = fetch_changes(fetch_ids)
            aliased = alias_batch_fetch_results(fetch_ids, fetched)
            for ref in fetch_ids:
                if ref in aliased:
                    out[ref] = aliased[ref]

        if fetch_ids or (scope_key and not refresh and out and did_network):
            with self._connect() as conn:
                if fetch_ids:
                    self.upsert_changes(aliased, conn=conn)
                    for ref in fetch_ids:
                        footer = footer_of_triplet(ref)
                        if ref in aliased:
                            self._clear_missing(conn, footer)
                        else:
                            self._mark_missing(conn, footer)
                if scope_key and not refresh and out and did_network:
                    self._maybe_certify_scope(conn, scope_key, triplets, out)

        return out

    def _try_delta(
        self,
        triplets: list[str],
        rows: dict[str, _ChangeRow],
        fetch_delta: Callable[[str], tuple[list[dict[str, Any]], bool]],
        scope_key: str,
        out: dict[str, dict[str, Any]],
    ) -> set[str] | None:
        """Apply a ``since:`` delta when every requested row is certified under *scope_key*.

        Returns the Gerrit ids the delta actually returned, or ``None`` when the
        delta cannot answer (uncertified rows, incomplete page). An empty set means
        the query succeeded but ``updated`` did not move — the caller must still probe
        those ids. Network I/O runs with no open SQLite connection.
        """

        with self._connect() as conn:
            state = self._get_scope_state(conn, scope_key)
        if state is None or not state.watermark:
            return None
        for triplet in triplets:
            row = rows.get(triplet)
            if row is None or row.certified_gen != state.generation:
                return None

        delta_rows, complete = fetch_delta(state.watermark)
        if not complete:
            return None

        new_watermark = max_updated(delta_rows, floor=state.watermark)
        new_gen = state.generation + 1
        with self._connect() as conn:
            if delta_rows:
                self.upsert_changes(delta_rows, conn=conn)
            self._set_scope_state(conn, scope_key, new_watermark, new_gen)
            self._stamp_certified_gen(conn, triplets, new_gen)

        covered = {tid for payload in delta_rows if (tid := payload_triplet(payload))}
        fresh = self._lookup_changes(triplets)
        for triplet in triplets:
            row = fresh.get(triplet)
            if row is not None:
                out[triplet] = row.payload
        return covered

    def _maybe_certify_scope(
        self,
        conn: sqlite3.Connection,
        scope_key: str,
        triplets: list[str],
        out: dict[str, dict[str, Any]],
    ) -> None:
        present = [triplet for triplet in triplets if triplet in out]
        if not present:
            return
        state = self._get_scope_state(conn, scope_key) or _ScopeState(watermark="", generation=0)
        payloads = [out[triplet] for triplet in present]
        new_watermark = max_updated(payloads, floor=state.watermark)
        if not new_watermark:
            return
        new_gen = state.generation + 1
        self._set_scope_state(conn, scope_key, new_watermark, new_gen)
        self._stamp_certified_gen(conn, present, new_gen)

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
            (key, payload_number(payload), _json_dumps(payload), payload_updated(payload), now)
            for key, payload in stored
        ]
        insert = """
            INSERT OR REPLACE INTO changes(change_id, number, payload, updated, fetched_at, certified_gen)
            VALUES (?, ?, ?, ?, ?, NULL)
        """
        if conn is not None:
            conn.executemany(insert, rows)
        else:
            with self._connect() as connection:
                connection.executemany(insert, rows)

    def invalidate_changes(self, triplets: list[str]) -> None:
        """Drop cached change, comment, checks and "no such change" rows for *triplets*."""
        if not triplets:
            return
        with self._connect() as conn:
            for table in ("changes", *_FOLLOW_UP_KEYS):
                conn.execute(f"DELETE FROM {table} WHERE change_id IN ({_placeholders(triplets)})", triplets)
            for triplet in triplets:
                self._clear_missing(conn, footer_of_triplet(triplet))

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
        where = " AND ".join(f"{column} = ?" for column in _FOLLOW_UP_KEYS[table])
        with self._connect() as conn:
            raw = conn.execute(f"SELECT payload, fetched_at, change_updated FROM {table} WHERE {where}", key).fetchone()
        if raw and not refresh:
            cached = decode(json.loads(str(raw["payload"])))
            # The freshness token decides when there is one; otherwise only the trust window can.
            if change_updated is not None:
                current = raw["change_updated"] == change_updated
            else:
                current = _now() - int(raw["fetched_at"]) < trust_window_seconds
            if cached is not None and current:
                return cached
        fresh = fetch()
        store(fresh)
        return fresh

    def _store_follow_up(self, table: str, key: tuple[str, ...], payload: Any, change_updated: str | None) -> None:
        columns = (*_FOLLOW_UP_KEYS[table], "payload", "fetched_at", "change_updated")
        with self._connect() as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO {table}({','.join(columns)}) VALUES ({_placeholders(columns)})",
                (*key, _json_dumps(payload), _now(), change_updated),
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
