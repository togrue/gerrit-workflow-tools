"""SQLite-backed Gerrit API cache."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gerrit_workflow_tools.core.gerrit.paths import gerrit_cache_db_path, gerrit_cache_host
from gerrit_workflow_tools.core.gerrit.rest import change_id_for_gerrit_rest_path

SCHEMA_VERSION = "1"
DEFAULT_CHANGE_TRUST_WINDOW_SECONDS = 10
DEFAULT_ACCOUNT_TTL_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class CacheInfo:
    """Small summary for cache administration commands."""

    path: Path
    host: str
    changes: int
    accounts: int
    comments: int


@dataclass(frozen=True)
class _ChangeRow:
    payload: dict[str, Any]
    updated: str | None
    fetched_at: int


@dataclass(frozen=True)
class _AccountRow:
    payload: dict[str, Any]
    fetched_at: int


@dataclass(frozen=True)
class _CommentRow:
    payload: dict[str, list[dict[str, Any]]]
    fetched_at: int
    change_updated: str | None


def _now() -> int:
    return int(time.time())


def _json_dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _change_key(change_id: str) -> str:
    return change_id_for_gerrit_rest_path(change_id)


def _payload_change_id(payload: dict[str, Any], fallback: str) -> str:
    raw = payload.get("change_id")
    if isinstance(raw, str):
        return _change_key(raw)
    return _change_key(fallback)


def _payload_updated(payload: dict[str, Any]) -> str | None:
    raw = payload.get("updated")
    return raw if isinstance(raw, str) else None


def _payload_number(payload: dict[str, Any]) -> int | None:
    raw = payload.get("_number")
    return raw if isinstance(raw, int) else None


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
        conn.execute("DROP TABLE IF EXISTS comments")
        conn.execute("DROP TABLE IF EXISTS accounts")
        conn.execute("DROP TABLE IF EXISTS changes")
        conn.execute("DROP TABLE IF EXISTS meta")

    @staticmethod
    def _create_tables(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS changes (
              change_id  TEXT PRIMARY KEY,
              number     INTEGER,
              payload    TEXT NOT NULL,
              updated    TEXT,
              fetched_at INTEGER NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS changes_number ON changes(number)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS accounts (
              account_id INTEGER PRIMARY KEY,
              username   TEXT,
              email      TEXT,
              name       TEXT,
              payload    TEXT NOT NULL,
              fetched_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS comments (
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
            conn.execute("DELETE FROM comments")
            conn.execute("DELETE FROM accounts")
            conn.execute("DELETE FROM changes")

    def info(self) -> CacheInfo:
        """Return row counts for cache administration."""

        with self._connect() as conn:
            changes = int(conn.execute("SELECT COUNT(*) FROM changes").fetchone()[0])
            accounts = int(conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0])
            comments = int(conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0])
        return CacheInfo(path=self.path, host=self.host, changes=changes, accounts=accounts, comments=comments)

    def _lookup_changes(self, change_ids: list[str]) -> dict[str, _ChangeRow]:
        keys = [_change_key(cid) for cid in change_ids]
        if not keys:
            return {}
        placeholders = ",".join("?" for _ in keys)
        out: dict[str, _ChangeRow] = {}
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT change_id, payload, updated, fetched_at FROM changes WHERE change_id IN ({placeholders})",
                keys,
            ).fetchall()
        for row in rows:
            payload = json.loads(str(row["payload"]))
            if isinstance(payload, dict):
                out[str(row["change_id"])] = _ChangeRow(
                    payload=payload,
                    updated=row["updated"] if isinstance(row["updated"], str) else None,
                    fetched_at=int(row["fetched_at"]),
                )
        return out

    def load_changes(
        self,
        change_ids: list[str],
        *,
        probe_updated: Callable[[list[str]], dict[str, str]],
        fetch_changes: Callable[[list[str]], dict[str, dict[str, Any]]],
        trust_window_seconds: int = DEFAULT_CHANGE_TRUST_WINDOW_SECONDS,
        refresh: bool = False,
    ) -> dict[str, dict[str, Any]]:
        """Load ChangeInfo payloads using cache, freshness probe, and batched fetch fallback."""

        keys = [_change_key(cid) for cid in change_ids]
        rows = self._lookup_changes(keys)
        now = _now()
        out: dict[str, dict[str, Any]] = {}
        probe_ids: list[str] = []
        fetch_ids: list[str] = []

        for key in keys:
            row = rows.get(key)
            if row is None:
                fetch_ids.append(key)
            elif not refresh and now - row.fetched_at < trust_window_seconds:
                out[key] = row.payload
            else:
                probe_ids.append(key)

        if probe_ids:
            updated_by_id = probe_updated(probe_ids)
            for key in probe_ids:
                row = rows[key]
                if row.updated and updated_by_id.get(key) == row.updated:
                    out[key] = row.payload
                else:
                    fetch_ids.append(key)

        if fetch_ids:
            fetched = fetch_changes(fetch_ids)
            self.upsert_changes(fetched)
            out.update(fetched)

        return out

    def upsert_changes(self, changes: dict[str, dict[str, Any]] | list[dict[str, Any]]) -> None:
        """Store ChangeInfo payloads."""

        items = list(changes.items()) if isinstance(changes, dict) else [("", payload) for payload in changes]
        now = _now()
        with self._connect() as conn:
            for fallback, payload in items:
                cid = _payload_change_id(payload, fallback)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO changes(change_id, number, payload, updated, fetched_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (cid, _payload_number(payload), _json_dumps(payload), _payload_updated(payload), now),
                )

    def invalidate_changes(self, change_ids: list[str]) -> None:
        """Drop cached change and comment rows for *change_ids*."""

        keys = [_change_key(cid) for cid in change_ids]
        if not keys:
            return
        placeholders = ",".join("?" for _ in keys)
        with self._connect() as conn:
            conn.execute(f"DELETE FROM changes WHERE change_id IN ({placeholders})", keys)
            conn.execute(f"DELETE FROM comments WHERE change_id IN ({placeholders})", keys)

    def load_comments(
        self,
        change_id: str,
        *,
        fetch_comments: Callable[[str], dict[str, list[dict[str, Any]]]],
        change_updated: str | None = None,
        trust_window_seconds: int = DEFAULT_CHANGE_TRUST_WINDOW_SECONDS,
        refresh: bool = False,
    ) -> dict[str, list[dict[str, Any]]]:
        """Load comment payloads with trust-window and optional change-updated validation."""

        key = _change_key(change_id)
        now = _now()
        row: _CommentRow | None = None
        with self._connect() as conn:
            raw = conn.execute(
                "SELECT payload, fetched_at, change_updated FROM comments WHERE change_id = ?",
                (key,),
            ).fetchone()
        if raw:
            payload = json.loads(str(raw["payload"]))
            if isinstance(payload, dict):
                file_map = {
                    str(k): [x for x in v if isinstance(x, dict)] for k, v in payload.items() if isinstance(v, list)
                }
                row = _CommentRow(
                    payload=file_map,
                    fetched_at=int(raw["fetched_at"]),
                    change_updated=raw["change_updated"] if isinstance(raw["change_updated"], str) else None,
                )
        if row and not refresh:
            if now - row.fetched_at < trust_window_seconds:
                return row.payload
            if change_updated is not None and row.change_updated == change_updated:
                return row.payload

        payload = fetch_comments(key)
        self.upsert_comments(key, payload, change_updated=change_updated)
        return payload

    def upsert_comments(
        self,
        change_id: str,
        payload: dict[str, list[dict[str, Any]]],
        *,
        change_updated: str | None = None,
    ) -> None:
        """Store comments for one change."""

        key = _change_key(change_id)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO comments(change_id, payload, fetched_at, change_updated)
                VALUES (?, ?, ?, ?)
                """,
                (key, _json_dumps(payload), _now(), change_updated),
            )

    def load_accounts(
        self,
        account_ids: list[int | str],
        *,
        fetch_accounts: Callable[[list[int | str]], dict[int, dict[str, Any]]],
        ttl_seconds: int = DEFAULT_ACCOUNT_TTL_SECONDS,
        refresh: bool = False,
    ) -> dict[int, dict[str, Any]]:
        """Load AccountInfo payloads with TTL-only freshness."""

        numeric_ids = [int(account_id) for account_id in account_ids]
        if not numeric_ids:
            return {}
        placeholders = ",".join("?" for _ in numeric_ids)
        now = _now()
        out: dict[int, dict[str, Any]] = {}
        fetch_ids: list[int | str] = []
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT account_id, payload, fetched_at FROM accounts WHERE account_id IN ({placeholders})",
                numeric_ids,
            ).fetchall()
        by_id: dict[int, _AccountRow] = {}
        for row in rows:
            payload = json.loads(str(row["payload"]))
            if isinstance(payload, dict):
                by_id[int(row["account_id"])] = _AccountRow(payload=payload, fetched_at=int(row["fetched_at"]))
        for account_id in numeric_ids:
            row = by_id.get(account_id)
            if row is not None and not refresh and now - row.fetched_at < ttl_seconds:
                out[account_id] = row.payload
            else:
                fetch_ids.append(account_id)
        if fetch_ids:
            fetched = fetch_accounts(fetch_ids)
            self.upsert_accounts(fetched)
            out.update(fetched)
        return out

    def upsert_accounts(self, accounts: dict[int, dict[str, Any]] | list[dict[str, Any]]) -> None:
        """Store AccountInfo payloads."""

        if isinstance(accounts, dict):
            rows = list(accounts.items())
        else:
            rows = []
            for payload in accounts:
                raw = payload.get("_account_id")
                if isinstance(raw, int):
                    rows.append((raw, payload))
        now = _now()
        with self._connect() as conn:
            for account_id, payload in rows:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO accounts(account_id, username, email, name, payload, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        account_id,
                        payload.get("username") if isinstance(payload.get("username"), str) else None,
                        payload.get("email") if isinstance(payload.get("email"), str) else None,
                        payload.get("name") if isinstance(payload.get("name"), str) else None,
                        _json_dumps(payload),
                        now,
                    ),
                )
