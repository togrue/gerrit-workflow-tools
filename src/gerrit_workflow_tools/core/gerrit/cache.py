"""SQLite-backed Gerrit API cache."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable, Iterable, Mapping, Sized
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from gerrit_workflow_tools.core.gerrit.cache_rows import (
    CacheInfo,
    decode_checks,
    decode_comments,
    footer_of_triplet,
    payload_number,
    payload_triplet,
    payload_updated,
    strip_avatars,
)
from gerrit_workflow_tools.core.gerrit.change_queries import alias_batch_fetch_results, change_freshness_key
from gerrit_workflow_tools.core.gerrit.paths import gerrit_cache_db_path, gerrit_cache_host


SCHEMA_VERSION = "5"
DEFAULT_CHANGE_TRUST_WINDOW_SECONDS = 10
DEFAULT_CAPABILITY_TTL_SECONDS = 7 * 24 * 60 * 60
DEFAULT_MISSING_CHANGE_TTL_SECONDS = 180
DEFAULT_SETTLED_COMMIT_TTL_SECONDS = 24 * 60 * 60

_CAPABILITY_PREFIX = "capability:"
_ACCOUNT_PREFIX = "account:"

_DATA_TABLES = ("checks", "comments", "changes", "missing_changes", "settled_commits")
# Follow-up tables and the columns that key one row.
_FOLLOW_UP_KEYS = {"comments": ("change_id", "context_key"), "checks": ("change_id",)}

_T = TypeVar("_T")


@dataclass(frozen=True)
class _ChangeRow:
    payload: dict[str, Any]
    verified_at: int


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
        for table in (*_DATA_TABLES, "meta", "accounts", "scope_state"):  # the last two are retired
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
              fetched_at     INTEGER NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS changes_number ON changes(number)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS changes_footer_change_id ON changes(json_extract(payload, '$.change_id'))"
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
            CREATE TABLE IF NOT EXISTS settled_commits (
              sha         TEXT PRIMARY KEY,
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
            for prefix in (_CAPABILITY_PREFIX, _ACCOUNT_PREFIX):
                conn.execute("DELETE FROM meta WHERE key LIKE ?", (f"{prefix}%",))

    def _host_fact(self, key: str, ttl_seconds: int) -> Any:
        """A fact about the host that rarely changes, or ``None`` when unknown or older than *ttl_seconds*."""
        with self._connect() as conn:
            raw = self._meta_get(conn, key)
        if not raw:
            return None
        try:
            record = json.loads(raw)
            value = record["value"]
            checked_at = int(record["at"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None
        return value if _now() - checked_at < ttl_seconds else None

    def _set_host_fact(self, key: str, value: Any) -> None:
        with self._connect() as conn:
            self._meta_set(conn, key, _json_dumps({"value": value, "at": _now()}))

    def capability(self, name: str, *, ttl_seconds: int = DEFAULT_CAPABILITY_TTL_SECONDS) -> bool | None:
        """Whether this host supports *name*, or ``None`` when unknown or stale."""
        value = self._host_fact(f"{_CAPABILITY_PREFIX}{name}", ttl_seconds)
        return value if isinstance(value, bool) else None

    def set_capability(self, name: str, value: bool) -> None:
        """Record whether this host supports *name*."""
        self._set_host_fact(f"{_CAPABILITY_PREFIX}{name}", value)

    def account_id(self, user: str, *, ttl_seconds: int = DEFAULT_CAPABILITY_TTL_SECONDS) -> int | None:
        """The account id Gerrit gave *user* (the configured ``gerrit.user``), or ``None`` when unknown or stale."""
        value = self._host_fact(f"{_ACCOUNT_PREFIX}{user}", ttl_seconds)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    def set_account_id(self, user: str, account_id: int) -> None:
        """Record the account id of *user*: it never changes, so nobody needs to ask on every run."""
        self._set_host_fact(f"{_ACCOUNT_PREFIX}{user}", account_id)

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
                FROM changes WHERE change_id IN ({_placeholders(triplets)})
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
                f"UPDATE changes SET fetched_at = ? WHERE change_id IN ({_placeholders(triplets)})",
                [_now(), *triplets],
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
        trust_window_seconds: int = DEFAULT_CHANGE_TRUST_WINDOW_SECONDS,
        missing_ttl_seconds: int = DEFAULT_MISSING_CHANGE_TTL_SECONDS,
        refresh: bool = False,
    ) -> dict[str, dict[str, Any]]:
        """Load ChangeInfo payloads keyed by Gerrit triplet ``id``.

        A row inside its trust window is served as it is. Past it, one probe asks Gerrit for
        the freshness tokens of those rows: a row whose token did not move is served and its
        trust window starts again, the others are fetched. So an unchanged stack costs one
        light request, and nothing at all for the next *trust_window_seconds*.

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
                elif not refresh and now - row.verified_at < trust_window_seconds:
                    out[triplet] = row.payload
                else:
                    probe_ids.append(triplet)

        if probe_ids:
            tokens = probe_updated(probe_ids)
            # Gerrit answers under its own id, which is not always the key that was asked for.
            asked = {key: tokens.get(key) or tokens.get(payload_triplet(rows[key].payload) or "") for key in probe_ids}
            current, moved = self.reconcile(asked)
            out.update(current)
            fetch_ids.extend(moved)

        if fetch_ids:
            aliased = alias_batch_fetch_results(fetch_ids, fetch_changes(fetch_ids))
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

    def settled_commits(
        self, shas: Iterable[str], *, ttl_seconds: int = DEFAULT_SETTLED_COMMIT_TTL_SECONDS
    ) -> set[str]:
        """Those of *shas* recently found to be no open change: history a review chain sits on."""
        wanted = list(shas)
        if not wanted:
            return set()
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT sha FROM settled_commits WHERE checked_at > ? AND sha IN ({_placeholders(wanted)})",
                [_now() - ttl_seconds, *wanted],
            ).fetchall()
        return {str(row["sha"]) for row in rows}

    def settle_commits(self, shas: Iterable[str]) -> None:
        """Record that Gerrit has no open change for *shas* (merged, or never a change)."""
        now = _now()
        with self._connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO settled_commits(sha, checked_at) VALUES (?, ?)", [(sha, now) for sha in shas]
            )

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
            INSERT OR REPLACE INTO changes(change_id, number, payload, updated, fetched_at)
            VALUES (?, ?, ?, ?, ?)
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

    def forget_change(self, number: int, change_id: str | None = None) -> bool:
        """Drop every row cached for change *number*, under any key, and a "no such change" for *change_id*.

        For news that the change moved (an event): it only ever narrows what is trusted.
        Returns whether anything was cached.
        """
        with self._connect() as conn:
            keys = [str(row[0]) for row in conn.execute("SELECT change_id FROM changes WHERE number = ?", (number,))]
            cleared = conn.execute("DELETE FROM missing_changes WHERE change_id = ?", (change_id or "",)).rowcount
        self.invalidate_changes(keys)
        return bool(keys) or cleared > 0

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
