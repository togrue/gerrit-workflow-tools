"""What the Gerrit cache remembers that is not a change: facts that rarely or never move.

Whether the host has a capability (the Checks plugin), the account id of the configured user,
and commits Gerrit has no open change for. Each has its own lifetime, far longer than the
trust window of a change, because asking again would almost always give the same answer.
:class:`~gerrit_workflow_tools.core.gerrit.cache.GerritCache` mixes this in and brings the
database connection and the clock.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from typing import Any

from gerrit_workflow_tools.core.gerrit.cache_rows import json_dumps
from gerrit_workflow_tools.core.gerrit.cache_schema import meta_get, meta_set, placeholders


DEFAULT_CAPABILITY_TTL_SECONDS = 7 * 24 * 60 * 60
DEFAULT_SETTLED_COMMIT_TTL_SECONDS = 24 * 60 * 60

_CAPABILITY_PREFIX = "capability:"
_ACCOUNT_PREFIX = "account:"

# Keys in ``meta`` that hold facts: `ger cache clear` forgets them with the data.
FACT_PREFIXES = (_CAPABILITY_PREFIX, _ACCOUNT_PREFIX)


class RememberedFacts:
    """Host capabilities, the user's account id and settled commits, each with its lifetime."""

    def _connect(self) -> sqlite3.Connection:
        raise NotImplementedError

    def _clock(self) -> int:
        raise NotImplementedError

    def _host_fact(self, key: str, ttl_seconds: int) -> Any:
        """A fact about the host that rarely changes, or ``None`` when unknown or older than *ttl_seconds*."""
        with self._connect() as conn:
            raw = meta_get(conn, key)
        if not raw:
            return None
        try:
            record = json.loads(raw)
            value = record["value"]
            checked_at = int(record["at"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None
        return value if self._clock() - checked_at < ttl_seconds else None

    def _set_host_fact(self, key: str, value: Any) -> None:
        with self._connect() as conn:
            meta_set(conn, key, json_dumps({"value": value, "at": self._clock()}))

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

    def settled_commits(
        self, shas: Iterable[str], *, ttl_seconds: int = DEFAULT_SETTLED_COMMIT_TTL_SECONDS
    ) -> set[str]:
        """Those of *shas* recently found to be no open change: history a review chain sits on."""
        wanted = list(shas)
        if not wanted:
            return set()
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT sha FROM settled_commits WHERE checked_at > ? AND sha IN ({placeholders(wanted)})",
                [self._clock() - ttl_seconds, *wanted],
            ).fetchall()
        return {str(row["sha"]) for row in rows}

    def settle_commits(self, shas: Iterable[str]) -> None:
        """Record that Gerrit has no open change for *shas* (merged, or never a change)."""
        now = self._clock()
        with self._connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO settled_commits(sha, checked_at) VALUES (?, ?)", [(sha, now) for sha in shas]
            )
