"""The tables of the Gerrit cache, and the version that says when an old database is dropped.

Rows are disposable: a schema change bumps :data:`SCHEMA_VERSION`, and
:class:`~gerrit_workflow_tools.core.gerrit.cache.GerritCache` drops and recreates everything
on first use. There are no migrations.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sized


SCHEMA_VERSION = "5"

# Tables that hold what Gerrit said. `ger cache clear` empties these; ``meta`` stays.
DATA_TABLES = ("checks", "comments", "changes", "missing_changes", "settled_commits")

# Tables of earlier schemas, dropped with the rest on a version change.
_RETIRED_TABLES = ("accounts", "scope_state")


def drop_tables(conn: sqlite3.Connection) -> None:
    """Drop every table, of this schema and of earlier ones."""
    for table in (*DATA_TABLES, "meta", *_RETIRED_TABLES):
        conn.execute(f"DROP TABLE IF EXISTS {table}")


def create_tables(conn: sqlite3.Connection) -> None:
    """Create the tables and indexes that are not there yet."""
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
    conn.execute("CREATE INDEX IF NOT EXISTS changes_footer_change_id ON changes(json_extract(payload, '$.change_id'))")
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


def placeholders(values: Sized) -> str:
    """``?,?,?`` for an ``IN (...)`` over *values*."""
    return ",".join("?" * len(values))


def meta_get(conn: sqlite3.Connection, key: str) -> str | None:
    """The value stored under *key* in ``meta``, or ``None``."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row else None


def meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Store *value* under *key* in ``meta``."""
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))
