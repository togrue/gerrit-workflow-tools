"""ChangeStore — a :class:`~gerrit_workflow_tools.core.gerrit.rest.GerritRest` backed by payloads.

Lives under ``tests/`` because no shipped code path constructs one: it exists so tests and
integration replay can substitute at the Gerrit seam. Conformance to ``GerritRest`` is
therefore not covered by mypy (which runs on ``src/``) and is asserted at runtime instead —
see ``test_change_store.py::test_change_store_implements_every_gerrit_rest_operation``.

Answers Gerrit questions from a dictionary of ChangeInfo payloads instead of HTTP. The
payloads can be authored by hand (unit tests) or recorded from a real server (integration
replay); the store does not care which, which is what makes it one adapter with two data
sources rather than two adapters.

It is **stateful**: ``set_topic``, ``set_wip``, ``set_private``, ``set_reviewers_batch`` and
``delete_reviewer`` mutate the stored payloads, so a subsequent read observes them. That is
what lets reviewer strategies be exercised through the seam rather than by asserting on mock
call records.

It also **records calls** (:attr:`calls`), for the few tests that care about *how* Gerrit was
asked rather than what came back — e.g. proving the stack overlay never falls back to
per-Change-Id queries.

Freshness is deliberately not modelled: writes do not bump ``updated``. The store answers
questions; deciding what is stale is the cache's job, above the seam.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from gerrit_workflow_tools.core.gerrit.rest import GerritApiError


DEFAULT_WEB_BASE = "https://gerrit.example"

_CHANGE_ID_SUFFIX_RE = re.compile(r"~(I[a-fA-F0-9]{40})$")
_PROJECT_SPLIT_RE = re.compile(r"(?=project:)")


@dataclass(frozen=True)
class RecordedCall:
    """One call made through the store."""

    method: str
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)


class ChangeStore:
    """A :class:`GerritRest` over ChangeInfo payloads keyed by Gerrit ``id``."""

    def __init__(
        self,
        changes: dict[str, dict[str, Any]] | None = None,
        *,
        web_base: str = DEFAULT_WEB_BASE,
        accounts: dict[int, dict[str, Any]] | None = None,
        comments: dict[str, dict[str, list[dict[str, Any]]]] | None = None,
        checks: dict[str, list[dict[str, Any]]] | None = None,
        messages: dict[str, list[dict[str, Any]]] | None = None,
        project_reviewers: dict[str, list[dict[str, Any]]] | None = None,
        self_account_id: int | None = None,
    ) -> None:
        self.web_base = web_base.rstrip("/")
        self._changes: dict[str, dict[str, Any]] = dict(changes or {})
        self._accounts: dict[int, dict[str, Any]] = dict(accounts or {})
        self._self_account_id = self_account_id
        self._comments: dict[str, dict[str, list[dict[str, Any]]]] = dict(comments or {})
        self._checks: dict[str, list[dict[str, Any]]] = dict(checks or {})
        self._messages: dict[str, list[dict[str, Any]]] = dict(messages or {})
        self._project_reviewers = dict(project_reviewers) if project_reviewers is not None else None
        self._stubbed_queries: dict[str, list[dict[str, Any]]] = {}
        self._next_account_id = 9000
        self.calls: list[RecordedCall] = []

    # -- seeding and inspection ------------------------------------------------

    def stub_query(self, query: str, rows: list[dict[str, Any]]) -> None:
        """Return *rows* verbatim for an exact *query* string.

        For searches the payload engine below does not model (arbitrary Gerrit search
        syntax such as ``status:open``). Prefer seeding payloads where possible.
        """
        self._stubbed_queries[query] = rows

    def set_comments(self, change_ref: str, file_map: dict[str, list[dict[str, Any]]]) -> None:
        """Seed inline comments for one change."""
        self._comments[self._payload_id(change_ref)] = file_map

    def set_checks(self, change_ref: str, rows: list[dict[str, Any]]) -> None:
        """Seed Checks-plugin rows for one change."""
        self._checks[self._payload_id(change_ref)] = rows

    def set_messages(self, change_ref: str, rows: list[dict[str, Any]]) -> None:
        """Seed change message rows for one change."""
        self._messages[self._payload_id(change_ref)] = rows

    def calls_to(self, method: str) -> list[RecordedCall]:
        """Recorded calls to one method, in order."""
        return [call for call in self.calls if call.method == method]

    def queries(self) -> list[str]:
        """Every Gerrit search query string this store was asked, in order."""
        return [str(call.args[0]) for call in self.calls_to("query_changes") if call.args]

    # -- internals -------------------------------------------------------------

    def _record(self, method: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append(RecordedCall(method=method, args=args, kwargs=kwargs))

    def _lookup_rows(self, ref: str) -> list[dict[str, Any]]:
        """Rows matching a Gerrit id, numeric change id, triplet, or bare Change-Id."""
        if ref in self._changes:
            return [self._changes[ref]]
        if ref.isdigit():
            rows = [row for row in self._changes.values() if row.get("_number") == int(ref)]
            if rows:
                return rows
        match = _CHANGE_ID_SUFFIX_RE.search(ref)
        if match:
            suffix = match.group(1)
            rows = [row for row in self._changes.values() if row.get("change_id") == suffix]
            if rows:
                return rows
        return [row for row in self._changes.values() if row.get("change_id") == ref]

    def _row_or_raise(self, ref: str) -> dict[str, Any]:
        rows = self._lookup_rows(ref)
        if not rows:
            raise GerritApiError(f"no matching change {ref}")
        return rows[0]

    def _payload_id(self, ref: str) -> str:
        rows = self._lookup_rows(ref)
        if rows:
            payload_id = rows[0].get("id")
            if isinstance(payload_id, str) and payload_id:
                return payload_id
        return ref

    def _query_rows(self, query: str) -> list[dict[str, Any]]:
        """Evaluate the query shapes this project actually issues.

        Handles project-scoped ``Change-Id`` OR batches (the stack overlay), exact
        ``project:P branch:B change:I`` triplet scoping, bare ``change:`` lookups, and
        ``since:`` delta freshness queries.
        """
        since_match = re.search(r'since:"([^"]+)"', query) or re.search(r"since:(\S+)", query)
        if since_match:
            since_val = since_match.group(1).rstrip(")")
            project_match = re.search(r"project:(\S+)", query)
            project = project_match.group(1) if project_match else None
            since_result: list[dict[str, Any]] = []
            seen: set[tuple[str, Any]] = set()
            for row in self._changes.values():
                if project is not None and row.get("project") != project:
                    continue
                updated = row.get("updated")
                if not isinstance(updated, str) or updated < since_val:
                    continue
                key = (str(row.get("id") or ""), row.get("_number"))
                if key in seen:
                    continue
                seen.add(key)
                since_result.append(row)
            since_result.sort(key=lambda row: str(row.get("updated") or ""))
            return since_result

        result: list[dict[str, Any]] = []
        seen_keys: set[tuple[str, Any]] = set()

        def _add(row: dict[str, Any] | None) -> None:
            if row is None:
                return
            key = (str(row.get("id") or ""), row.get("_number"))
            if key in seen_keys:
                return
            seen_keys.add(key)
            result.append(row)

        for part in _PROJECT_SPLIT_RE.split(query):
            part = part.strip().rstrip(")")
            if not part.startswith("project:"):
                continue
            match = re.match(r"project:(\S+)\s+(.*)", part, flags=re.DOTALL)
            if not match:
                continue
            project, rest = match.group(1), match.group(2).strip()
            if re.match(r"branch:\S+\s+change:", rest) and "(" not in rest:
                for branch, change_id in re.findall(r"branch:(\S+)\s+change:(\S+)", rest):
                    _add(self._changes.get(f"{project}~{branch}~{change_id.rstrip(')')}"))
                continue
            for change_id in re.findall(r"change:(\S+)", rest):
                change_id = change_id.rstrip(")")
                for row in self._changes.values():
                    if row.get("project") == project and row.get("change_id") == change_id:
                        _add(row)

        if "project:" not in query:
            for change_ref in re.findall(r"change:(\S+)", query):
                for row in self._lookup_rows(change_ref.rstrip(")")):
                    _add(row)

        for sha in re.findall(r"commit:(\S+)", query):
            needle = sha.rstrip(")").lower()
            for row in self._changes.values():
                current = row.get("current_revision")
                if isinstance(current, str) and current.lower() == needle:
                    _add(row)
                    continue
                revisions = row.get("revisions")
                if isinstance(revisions, dict) and any(
                    isinstance(key, str) and key.lower() == needle for key in revisions
                ):
                    _add(row)

        return result

    def _reviewer_rows(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Normalize a payload's ``reviewers`` (list or role-bucket form) to ReviewerInfo rows."""
        reviewers = payload.get("reviewers")
        if isinstance(reviewers, list):
            return [row for row in reviewers if isinstance(row, dict)]
        out: list[dict[str, Any]] = []
        if isinstance(reviewers, dict):
            for role in ("REVIEWER", "CC"):
                bucket = reviewers.get(role)
                if isinstance(bucket, list):
                    out.extend({"account": account, "state": role} for account in bucket if isinstance(account, dict))
        return out

    # -- GerritRest: reads -----------------------------------------------------

    def query_changes(self, query: str, *, n: int = 25, options: list[str] | None = None) -> list[dict[str, Any]]:
        """Return ChangeInfo rows matching a Gerrit search query."""
        self._record("query_changes", query, n=n, options=options)
        if query in self._stubbed_queries:
            return list(self._stubbed_queries[query])[:n]
        return self._query_rows(query)[:n]

    def query_accounts(self, query: str, *, n: int = 10) -> list[dict[str, Any]]:
        """Return AccountInfo rows whose username, email or name contains *query*."""
        self._record("query_accounts", query, n=n)
        needle = query.strip().lower()
        out = [
            payload
            for payload in self._accounts.values()
            if any(
                isinstance(payload.get(f), str) and needle in str(payload[f]).lower()
                for f in ("username", "email", "name")
            )
        ]
        return out[:n]

    def get_change(self, change_id: str) -> dict[str, Any]:
        """Return ChangeInfo detail for one change."""
        self._record("get_change", change_id)
        return self._row_or_raise(change_id)

    def get_account(self, account_id: int | str) -> dict[str, Any]:
        """Return AccountInfo detail for one account."""
        self._record("get_account", account_id)
        if isinstance(account_id, str) and account_id.lower() == "self":
            if self._self_account_id is not None:
                payload = self._accounts.get(self._self_account_id)
            else:
                payload = next(iter(self._accounts.values()), None)
            if payload is None:
                raise GerritApiError("no matching account self")
            return payload
        try:
            payload = self._accounts.get(int(account_id))
        except (TypeError, ValueError):
            payload = next(
                (row for row in self._accounts.values() if row.get("username") == account_id),
                None,
            )
        if payload is None:
            raise GerritApiError(f"no matching account {account_id}")
        return payload

    def get_comments(self, change_id: str) -> dict[str, list[dict[str, Any]]]:
        """Return inline comments grouped by file path."""
        self._record("get_comments", change_id)
        return self._comments.get(self._payload_id(change_id), {})

    def get_checks(self, change_id: str) -> list[dict[str, Any]]:
        """Return Checks-plugin rows for the current revision."""
        self._record("get_checks", change_id)
        return self._checks.get(self._payload_id(change_id), [])

    def get_messages(self, change_id: str) -> list[dict[str, Any]]:
        """Return change message rows for one change."""
        self._record("get_messages", change_id)
        return self._messages.get(self._payload_id(change_id), [])

    def list_change_reviewers(self, change_id: str) -> list[dict[str, Any]]:
        """Return reviewer rows for one change."""
        self._record("list_change_reviewers", change_id)
        return self._reviewer_rows(self._row_or_raise(change_id))

    def suggest_change_reviewers(
        self,
        change_id: str,
        *,
        query: str | None = None,
        n: int = 20,
    ) -> list[dict[str, Any]]:
        """Return suggested reviewer rows for one change."""
        self._record("suggest_change_reviewers", change_id, query=query, n=n)
        accounts = self.query_accounts(query, n=n) if query else list(self._accounts.values())[:n]
        return [{"account": payload} for payload in accounts]

    def get_plugin_project_reviewers(self, project: str) -> list[dict[str, Any]] | None:
        """Return project-level reviewer defaults, or ``None`` when the plugin is absent."""
        self._record("get_plugin_project_reviewers", project)
        if self._project_reviewers is None:
            return None
        return self._project_reviewers.get(project, [])

    # -- GerritRest: writes ----------------------------------------------------

    def set_reviewers_batch(
        self,
        change_id: str,
        *,
        reviewers: list[str] | None = None,
        ccs: list[str] | None = None,
    ) -> dict[str, Any]:
        """Add reviewers and CCs to one change; later reads observe them."""
        self._record("set_reviewers_batch", change_id, reviewers=reviewers, ccs=ccs)
        payload = self._row_or_raise(change_id)
        rows = self._reviewer_rows(payload)
        existing = {str(row.get("account", {}).get("username")) for row in rows if isinstance(row.get("account"), dict)}
        for names, state in ((reviewers or [], "REVIEWER"), (ccs or [], "CC")):
            for name in names:
                if name in existing:
                    continue
                existing.add(name)
                self._next_account_id += 1
                rows.append({"account": {"username": name, "_account_id": self._next_account_id}, "state": state})
        payload["reviewers"] = rows
        return {}

    def delete_reviewer(self, change_id: str, account_id: int) -> Any:
        """Remove one reviewer or CC from a change."""
        self._record("delete_reviewer", change_id, account_id)
        payload = self._row_or_raise(change_id)
        payload["reviewers"] = [
            row
            for row in self._reviewer_rows(payload)
            if not (isinstance(row.get("account"), dict) and row["account"].get("_account_id") == account_id)
        ]
        return None

    def set_topic(self, change_id: str, topic: str | None) -> None:
        """Set or clear the change topic."""
        self._record("set_topic", change_id, topic)
        self._row_or_raise(change_id)["topic"] = topic or None

    def set_wip(self, change_id: str, on: bool) -> dict[str, Any]:
        """Mark a change work-in-progress or ready for review."""
        self._record("set_wip", change_id, on)
        payload = self._row_or_raise(change_id)
        payload["work_in_progress"] = on
        return payload

    def set_private(self, change_id: str, on: bool) -> dict[str, Any]:
        """Set or clear the private flag on a change."""
        self._record("set_private", change_id, on)
        payload = self._row_or_raise(change_id)
        payload["private"] = on
        return payload
