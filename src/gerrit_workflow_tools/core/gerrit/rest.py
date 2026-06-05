"""Low-level Gerrit REST API access."""

from __future__ import annotations

import base64
import json
import logging
import re
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, TypeVar
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from gerrit_workflow_tools.core.config import gerrit_password, gerrit_token, gerrit_user, gerrit_web_url
from gerrit_workflow_tools.core.git_run import GitError

logger = logging.getLogger(__name__)
_LOG_RESPONSE_BODIES = False
_T = TypeVar("_T")


def set_log_gerrit_response_bodies(enabled: bool) -> None:
    """Configure whether full Gerrit JSON payloads should be debug-logged."""

    global _LOG_RESPONSE_BODIES  # pylint: disable=global-statement
    _LOG_RESPONSE_BODIES = enabled


class GerritApiError(RuntimeError):
    """Gerrit HTTP or JSON error."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


_CHANGE_ID_REST_PATH_RE = re.compile(r"^[iI]([0-9a-fA-F]{40})$")
_BATCH_OR_CHUNK = 25


def norm_change_id(change_id: str) -> str:
    """Normalize Change-Id values for case-insensitive lookups (lowercase)."""
    return change_id.lower()


def change_id_for_gerrit_rest_path(change_id: str) -> str:
    """
    Return *change_id* for Gerrit ``changes/<id>/...`` URL segments.

    Gerrit expects the canonical Change-Id with an uppercase ``I`` prefix; values
    taken from :func:`norm_change_id` use a lowercase ``i`` and yield HTTP 404 unless corrected.
    """

    s = change_id.strip()
    m = _CHANGE_ID_REST_PATH_RE.fullmatch(s)
    if m:
        return "I" + m.group(1).lower()
    return s


def _strip_magic_json_prefix(raw: str) -> str:
    s = raw.lstrip()
    if s.startswith(")]}'"):
        nl = s.find("\n")
        if nl != -1:
            return s[nl + 1 :]
    return raw


def _basic_auth_header(cwd: str | None) -> str | None:
    user = gerrit_user(cwd)
    secret = gerrit_token(cwd) or gerrit_password(cwd)
    if not user or secret is None:
        return None
    token = base64.b64encode(f"{user}:{secret}".encode()).decode()
    return f"Basic {token}"


def parallel_map(
    callables: Iterable[Callable[[], _T]],
    *,
    max_workers: int = 8,
) -> list[_T]:
    """Run blocking REST callables concurrently and preserve input order."""

    jobs = list(callables)
    if not jobs:
        return []
    workers = min(max_workers, len(jobs))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(lambda fn: fn(), jobs))


class GerritClient:
    """HTTP client for Gerrit REST ``/a/`` endpoints using git-config credentials."""

    def __init__(self, web_base: str, *, cwd: str | None = None) -> None:
        """Use *web_base* (HTTPS origin) and optional *cwd* for resolving ``gerrit.user`` / token config."""
        self.web_base = web_base.rstrip("/")
        self.cwd = cwd

    def _auth_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "*/*"}
        auth = _basic_auth_header(self.cwd)
        if auth:
            headers["Authorization"] = auth
        else:
            raise GerritApiError(
                "missing Gerrit credentials in git config; set gerrit.user and gerrit.password (or gerrit.token)"
            )
        return headers

    def _url(self, path: str, *, params: dict[str, str] | list[tuple[str, str]] | None) -> str:
        if params is None:
            q = ""
        elif isinstance(params, list):
            q = f"?{urlencode(params, doseq=True)}"
        else:
            q = f"?{urlencode(params)}"
        return f"{self.web_base}/a/{path.lstrip('/')}{q}"

    def _request_json(
        self,
        path: str,
        *,
        method: str = "GET",
        params: dict[str, str] | list[tuple[str, str]] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        url = self._url(path, params=params)
        headers = self._auth_headers()
        data: bytes | None = None
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=UTF-8"
        logger.info("%s %s", method, url)
        req = Request(url, headers=headers, method=method, data=data)
        try:
            with urlopen(req, timeout=120) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except HTTPError as e:
            raise GerritApiError(
                f"Gerrit HTTP {e.code} for {url}: {e.reason}",
                status=e.code,
            ) from e
        except URLError as e:
            raise GerritApiError(f"Gerrit request failed: {e.reason!r}") from e

        if not raw.strip():
            return {}
        try:
            parsed = json.loads(_strip_magic_json_prefix(raw))
        except json.JSONDecodeError as e:
            raise GerritApiError(f"invalid JSON from Gerrit: {e}") from e
        if _LOG_RESPONSE_BODIES:
            logger.debug("response body: %s", json.dumps(parsed, indent=2))
        return parsed

    def get_json(
        self,
        path: str,
        *,
        params: dict[str, str] | list[tuple[str, str]] | None = None,
    ) -> Any:
        """GET any path under ``/a/`` and return parsed JSON (same credentials as other methods)."""
        return self._request_json(path, params=params)

    def query_changes(
        self,
        query: str,
        *,
        n: int = 25,
        options: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """GET ``changes/?q=...`` and return a list of change dicts.

        *options* are passed as repeated ``o=`` query parameters (e.g. ``DETAILED_LABELS``).
        """
        parts: list[tuple[str, str]] = [("q", query), ("n", str(n))]
        if options:
            for opt in options:
                parts.append(("o", opt))
        data = self._request_json("changes/", params=parts)
        if not isinstance(data, list):
            raise GerritApiError("unexpected changes query response")
        logger.info("query_changes %r -> %d result(s)", query, len(data))
        return data

    def query_accounts(self, query: str, *, n: int = 10) -> list[dict[str, Any]]:
        """GET ``accounts/?q=...`` and return account rows."""
        data = self._request_json("accounts/", params=[("q", query), ("n", str(n))])
        if not isinstance(data, list):
            raise GerritApiError("unexpected accounts query response")
        out = [row for row in data if isinstance(row, dict)]
        logger.info("query_accounts %r -> %d result(s)", query, len(out))
        return out

    def get_account(self, account_id: int | str) -> dict[str, Any]:
        """GET account detail by Gerrit account id, username, email, or ``self``."""
        enc = quote(str(account_id), safe="")
        data = self._request_json(f"accounts/{enc}/detail")
        if not isinstance(data, dict):
            raise GerritApiError("unexpected account detail response")
        logger.info("get_account %r -> %s", account_id, data.get("_account_id"))
        return data

    def suggest_change_reviewers(
        self,
        change_id: str,
        *,
        query: str | None = None,
        n: int = 20,
    ) -> list[dict[str, Any]]:
        """GET suggested reviewers for ``change_id`` via ``changes/<id>/suggest_reviewers``."""
        cid = change_id_for_gerrit_rest_path(change_id)
        enc = quote(cid, safe="")
        params: list[tuple[str, str]] = [("n", str(n))]
        if query:
            params.insert(0, ("q", query))
        data = self._request_json(f"changes/{enc}/suggest_reviewers", params=params)
        if not isinstance(data, list):
            raise GerritApiError("unexpected suggest reviewers response")
        out = [row for row in data if isinstance(row, dict)]
        logger.info("suggest_change_reviewers %r -> %d result(s)", cid, len(out))
        return out

    def get_plugin_project_reviewers(self, project: str) -> list[dict[str, Any]] | None:
        """GET project-level reviewer defaults from reviewers plugin (if installed)."""
        enc = quote(project, safe="")
        try:
            data = self._request_json(f"projects/{enc}/reviewers")
        except GerritApiError as e:
            if e.status == 404:
                return None
            raise
        if not isinstance(data, list):
            raise GerritApiError("unexpected project reviewers response")
        out = [row for row in data if isinstance(row, dict)]
        logger.info("get_plugin_project_reviewers %r -> %d result(s)", project, len(out))
        return out

    def get_change(self, change_id: str) -> dict[str, Any]:
        """GET change detail (labels, submittable, etc.) for *change_id*."""
        cid = change_id_for_gerrit_rest_path(change_id)
        enc = quote(cid, safe="")
        data = self._request_json(f"changes/{enc}/detail")
        if not isinstance(data, dict):
            raise GerritApiError("unexpected change detail response")
        logger.info(
            "get_change %r -> #%s %r",
            cid,
            data.get("_number"),
            data.get("subject"),
        )
        return data

    def list_change_reviewers(self, change_id: str) -> list[dict[str, Any]]:
        """GET ``changes/<id>/reviewers/`` (lighter than full ``/detail``)."""
        cid = change_id_for_gerrit_rest_path(change_id)
        enc = quote(cid, safe="")
        data = self._request_json(f"changes/{enc}/reviewers/")
        if not isinstance(data, list):
            raise GerritApiError("unexpected list reviewers response")
        out = [row for row in data if isinstance(row, dict)]
        logger.info("list_change_reviewers %r -> %d reviewer(s)", cid, len(out))
        return out

    def add_reviewer(self, change_id: str, reviewer: str) -> dict[str, Any]:
        """POST a reviewer (username or email) onto *change_id*."""
        data = self.set_reviewers_batch(change_id, reviewers=[reviewer])
        logger.info("add_reviewer %r -> %s", change_id_for_gerrit_rest_path(change_id), data.get("_account_id"))
        return data

    def set_reviewers_batch(
        self,
        change_id: str,
        *,
        reviewers: list[str] | None = None,
        ccs: list[str] | None = None,
    ) -> dict[str, Any]:
        """POST multiple reviewers/CCs onto *change_id* in one review request."""

        reviewer_inputs: list[dict[str, str]] = []
        reviewer_inputs.extend({"reviewer": reviewer} for reviewer in reviewers or [])
        reviewer_inputs.extend({"reviewer": cc, "state": "CC"} for cc in ccs or [])
        if not reviewer_inputs:
            return {}
        cid = change_id_for_gerrit_rest_path(change_id)
        enc = quote(cid, safe="")
        data = self._request_json(
            f"changes/{enc}/revisions/current/review",
            method="POST",
            json_body={"reviewers": reviewer_inputs},
        )
        if not isinstance(data, dict):
            raise GerritApiError("unexpected set reviewers response")
        logger.info("set_reviewers_batch %r -> %d reviewer input(s)", cid, len(reviewer_inputs))
        return data

    def delete_reviewer(self, change_id: str, account_id: int) -> Any:
        """Remove *account_id* from *change_id* (REVIEWER or CC)."""
        cid = change_id_for_gerrit_rest_path(change_id)
        enc = quote(cid, safe="")
        aid_enc = quote(str(account_id), safe="")
        return self._request_json(f"changes/{enc}/reviewers/{aid_enc}", method="DELETE")

    def set_topic(self, change_id: str, topic: str | None) -> None:
        """Set or clear the topic on *change_id*.

        Gerrit ``PUT /changes/{id}/topic`` returns the new topic as a bare JSON
        string (e.g. ``"my-topic"``), not a JSON object.  Clearing the topic
        yields ``204 No Content`` (mapped to ``{}`` by ``_request_json``).
        Both are valid; neither needs further validation.
        """
        cid = change_id_for_gerrit_rest_path(change_id)
        enc = quote(cid, safe="")
        self._request_json(f"changes/{enc}/topic", method="PUT", json_body={"topic": topic or ""})

    def set_wip(self, change_id: str, on: bool) -> dict[str, Any]:
        """Mark *change_id* work-in-progress when *on*, otherwise ready for review."""
        cid = change_id_for_gerrit_rest_path(change_id)
        enc = quote(cid, safe="")
        path = f"changes/{enc}/wip" if on else f"changes/{enc}/ready"
        data = self._request_json(path, method="POST", json_body={})
        if not isinstance(data, dict):
            raise GerritApiError("unexpected set WIP response")
        return data

    def set_private(self, change_id: str, on: bool) -> dict[str, Any]:
        """Mark *change_id* private when *on*, otherwise remove the private flag."""
        cid = change_id_for_gerrit_rest_path(change_id)
        enc = quote(cid, safe="")
        if on:
            data = self._request_json(f"changes/{enc}/private", method="POST", json_body={})
        else:
            data = self._request_json(f"changes/{enc}/private", method="DELETE")
        if not isinstance(data, dict):
            raise GerritApiError("unexpected set private response")
        return data

    def get_comments(self, change_id: str) -> dict[str, list[dict[str, Any]]]:
        """GET inline comments grouped by file path (or special keys) for *change_id*."""
        cid = change_id_for_gerrit_rest_path(change_id)
        enc = quote(cid, safe="")
        data = self._request_json(f"changes/{enc}/comments")
        if not isinstance(data, dict):
            raise GerritApiError("unexpected comments response")
        out: dict[str, list[dict[str, Any]]] = {}
        for k, v in data.items():
            if isinstance(v, list):
                out[k] = [x for x in v if isinstance(x, dict)]
        total = sum(len(v) for v in out.values())
        logger.info(
            "get_comments %r -> %d file(s), %d comment(s)",
            cid,
            len(out),
            total,
        )
        return out

    def probe_changes_updated(self, change_ids: list[str]) -> dict[str, str]:
        """Return Gerrit ``updated`` values keyed by canonical Change-Id for a cheap freshness check."""

        out: dict[str, str] = {}
        unique: list[str] = []
        seen: set[str] = set()
        for raw in change_ids:
            cid = change_id_for_gerrit_rest_path(raw)
            key = cid.lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(cid)

        def _probe_chunk(chunk: list[str]) -> list[dict[str, Any]]:
            q = " OR ".join(f"change:{cid}" for cid in chunk)
            return self.query_changes(q, n=len(chunk) + 10, options=["SKIP_DIFFSTAT"])

        def _probe_job(chunk: list[str]) -> Callable[[], list[dict[str, Any]]]:
            def _job() -> list[dict[str, Any]]:
                return _probe_chunk(chunk)

            return _job

        chunks = [unique[i : i + _BATCH_OR_CHUNK] for i in range(0, len(unique), _BATCH_OR_CHUNK)]
        jobs = [_probe_job(chunk) for chunk in chunks]
        rows_by_chunk = parallel_map(jobs)
        for rows in rows_by_chunk:
            for row in rows:
                raw_id = row.get("change_id")
                updated = row.get("updated")
                if isinstance(raw_id, str) and isinstance(updated, str):
                    out[change_id_for_gerrit_rest_path(raw_id)] = updated
        return out


def resolve_change_ref(arg: str) -> str:
    """Build a ``changes/`` query string (numeric id, Change-Id, or passthrough)."""
    s = arg.strip()
    if re.fullmatch(r"\d+", s):
        return f"change:{s}"
    if s.upper().startswith("I") and len(s) == 41:
        return f"change:{s}"
    return s


def pick_change_from_query_result(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the single change from *rows* or raise :class:`GerritApiError` if none or ambiguous."""
    if not rows:
        raise GerritApiError("no matching change")
    if len(rows) > 1:
        nums = [str(r.get("_number", "?")) for r in rows[:5]]
        raise GerritApiError(f"ambiguous change query ({len(rows)} matches): {', '.join(nums)}")
    return rows[0]


def resolve_gerrit_web_base(cwd: Path | str | None) -> str:
    """
    Gerrit HTTPS base for the REST API and web links.

    Requires ``gerrit.webUrl`` in git config (no inference from remotes).
    """
    override = gerrit_web_url(cwd)
    if override:
        base = override.rstrip("/")
        logger.debug("resolve_gerrit_web_base: gerrit.webUrl -> %s", base)
        return base
    raise ValueError(
        "gerrit.webUrl is not set; configure the Gerrit HTTPS base, e.g. "
        "`git config gerrit.webUrl https://gerrit.example.com`"
    )


# Options for batch stack queries and single-change resolution:
# labels + submittable + revisions in one round trip (no separate /detail call).
LOG_QUERY_OPTIONS = (
    "DETAILED_LABELS",
    "SUBMITTABLE",
    "CURRENT_REVISION",
    "ALL_REVISIONS",
)

_RESOLVE_CHANGE_QUERY_OPTIONS = LOG_QUERY_OPTIONS


def resolve_gerrit_change(
    client: GerritClient,
    *,
    change_arg: str | None,
    local_change_id: str | None,
) -> dict[str, Any]:
    """Resolve a Gerrit change query *change_arg* or *local_change_id* to a single change dict."""
    opts = list(_RESOLVE_CHANGE_QUERY_OPTIONS)
    if change_arg:
        q = resolve_change_ref(change_arg)
        rows = client.query_changes(q, n=10, options=opts)
        ch = pick_change_from_query_result(rows)
    elif local_change_id:
        rows = client.query_changes(f"change:{local_change_id}", n=10, options=opts)
        ch = pick_change_from_query_result(rows)
    else:
        raise GitError("internal: no change specified")
    logger.info(
        "resolved change -> #%s %r (id=%s)",
        ch.get("_number"),
        ch.get("subject"),
        ch.get("id"),
    )
    logger.debug("resolved change detail: %s", ch)
    return ch


# ---------------------------------------------------------------------------
# Batch change queries
# ---------------------------------------------------------------------------


def _ingest_change_rows(out: dict[str, dict[str, Any]], rows: list[Any]) -> None:
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_id = row.get("change_id")
        if isinstance(raw_id, str):
            out[norm_change_id(raw_id)] = row


def _fallback_query_chunk(client: GerritClient, chunk: list[str]) -> list[dict[str, Any]]:
    """Query each Change-Id in *chunk* when a batched OR query fails (same session, sequential)."""
    rows: list[dict[str, Any]] = []
    for change_id in chunk:
        one = query_single_change(client, change_id)
        if one:
            rows.append(one)
    return rows


def _query_change_chunk(client: GerritClient, chunk: list[str], opts: list[str]) -> list[dict[str, Any]]:
    q = " OR ".join(f"change:{c}" for c in chunk)
    try:
        return client.query_changes(q, n=len(chunk) + 10, options=opts)
    except GerritApiError as e:
        logger.warning("batched Gerrit query failed (%s), falling back per change", e)
        return _fallback_query_chunk(client, chunk)


def query_single_change(client: GerritClient, change_id: str) -> dict[str, Any] | None:
    """Query one Gerrit change by Change-Id and return first matching ``ChangeInfo`` row."""
    try:
        rows = client.query_changes(f"change:{change_id}", n=5, options=list(LOG_QUERY_OPTIONS))
    except GerritApiError as e:
        logger.warning("Gerrit query failed for %s: %s", change_id, e)
        return None
    if not rows:
        return None
    return rows[0]


def batch_load_change_details(client: GerritClient, change_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Map normalized Change-Id to ChangeInfo using chunked ``change:I1 OR change:I2`` queries."""
    out: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    unique: list[str] = []
    for cid in change_ids:
        k = norm_change_id(cid)
        if k not in seen:
            seen.add(k)
            unique.append(cid)

    opts = list(LOG_QUERY_OPTIONS)
    chunks = [unique[i : i + _BATCH_OR_CHUNK] for i in range(0, len(unique), _BATCH_OR_CHUNK)]

    def _chunk_job(chunk: list[str]) -> Callable[[], list[dict[str, Any]]]:
        def _job() -> list[dict[str, Any]]:
            return _query_change_chunk(client, chunk, opts)

        return _job

    for rows in parallel_map(_chunk_job(chunk) for chunk in chunks):
        _ingest_change_rows(out, rows)
    return out
