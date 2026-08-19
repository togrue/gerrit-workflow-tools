"""Low-level Gerrit REST API access."""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from gerrit_workflow_tools.core.changeish import Changeish, is_change_id, parse
from gerrit_workflow_tools.core.config import ConfigError, Settings

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
    if is_change_id(s):
        return "I" + s[1:].lower()
    return s


def _strip_magic_json_prefix(raw: str) -> str:
    s = raw.lstrip()
    if s.startswith(")]}'"):
        nl = s.find("\n")
        if nl != -1:
            return s[nl + 1 :]
    return raw


@dataclass(frozen=True)
class GerritAuth:
    """Resolved HTTP Basic credentials for one Gerrit host."""

    user: str
    secret: str

    def header(self) -> str:
        """Return the ``Authorization`` header value."""
        token = base64.b64encode(f"{self.user}:{self.secret}".encode()).decode()
        return f"Basic {token}"


def gerrit_auth_from_settings(settings: Settings) -> GerritAuth | None:
    """Read ``gerrit.user`` plus ``gerrit.token``/``gerrit.password``, or ``None`` when unset."""
    user = settings.gerrit_user
    secret = settings.gerrit_token or settings.gerrit_password
    if not user or secret is None:
        return None
    return GerritAuth(user=user, secret=secret)


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


class GerritRest(Protocol):
    """Single-round-trip Gerrit operations — one call in, one Gerrit payload out.

    This is the seam. Everything that composes round trips — chunking, ``OR`` batching,
    triplet aliasing, the SQLite cache, parallelism — sits *above* it in
    :mod:`~gerrit_workflow_tools.core.gerrit.service` and the module-level helpers below,
    so an implementation only has to answer individual Gerrit questions.

    Implementations return Gerrit payloads verbatim and raise :class:`GerritApiError` on
    failure; interpreting or filtering those payloads is the caller's job. There is
    deliberately no raw-path escape hatch (``get_json``) — that would make the set of
    things crossing this seam unbounded. ``cli_fetch_api`` uses a concrete
    :class:`HttpGerritRest` for exactly that reason.
    """

    web_base: str

    def query_changes(self, query: str, *, n: int = 25, options: list[str] | None = None) -> list[dict[str, Any]]:
        """Return ChangeInfo rows matching a Gerrit search query."""

    def query_accounts(self, query: str, *, n: int = 10) -> list[dict[str, Any]]:
        """Return AccountInfo rows matching an account search query."""

    def get_change(self, change_id: str) -> dict[str, Any]:
        """Return ChangeInfo detail for one change."""

    def get_account(self, account_id: int | str) -> dict[str, Any]:
        """Return AccountInfo detail for one account."""

    def get_comments(self, change_id: str) -> dict[str, list[dict[str, Any]]]:
        """Return inline comments grouped by file path."""

    def get_checks(self, change_id: str) -> list[dict[str, Any]]:
        """Return Checks-plugin rows for the current revision."""

    def list_change_reviewers(self, change_id: str) -> list[dict[str, Any]]:
        """Return reviewer rows for one change."""

    def suggest_change_reviewers(
        self,
        change_id: str,
        *,
        query: str | None = None,
        n: int = 20,
    ) -> list[dict[str, Any]]:
        """Return suggested reviewer rows for one change."""

    def get_plugin_project_reviewers(self, project: str) -> list[dict[str, Any]] | None:
        """Return project-level reviewer defaults, or ``None`` when the plugin is absent."""

    def set_reviewers_batch(
        self,
        change_id: str,
        *,
        reviewers: list[str] | None = None,
        ccs: list[str] | None = None,
    ) -> dict[str, Any]:
        """Add reviewers and CCs to one change in a single request."""

    def delete_reviewer(self, change_id: str, account_id: int) -> Any:
        """Remove one reviewer or CC from a change."""

    def set_topic(self, change_id: str, topic: str | None) -> None:
        """Set or clear the change topic."""

    def set_wip(self, change_id: str, on: bool) -> dict[str, Any]:
        """Mark a change work-in-progress or ready for review."""

    def set_private(self, change_id: str, on: bool) -> dict[str, Any]:
        """Set or clear the private flag on a change."""


class HttpGerritRest:
    """HTTP client for Gerrit REST ``/a/`` endpoints using git-config credentials."""

    def __init__(self, web_base: str, *, auth: GerritAuth | None = None) -> None:
        """Use *web_base* (HTTPS origin) with credentials already resolved.

        Credentials are a construction-time input, not something read from git config per
        request — that is what keeps ``cwd`` off :class:`GerritRest`.
        """
        self.web_base = web_base.rstrip("/")
        self.auth = auth

    @classmethod
    def from_settings(cls, web_base: str, settings: Settings) -> HttpGerritRest:
        """Build a client with credentials taken from *settings*."""
        return cls(web_base, auth=gerrit_auth_from_settings(settings))

    def _auth_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "*/*"}
        if self.auth is None:
            raise GerritApiError(
                "missing Gerrit credentials in git config; set gerrit.user and gerrit.password (or gerrit.token)"
            )
        headers["Authorization"] = self.auth.header()
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

    def get_checks(self, change_id: str) -> list[dict[str, Any]]:
        """GET ``changes/<id>/revisions/current/checks`` (Checks plugin) and return raw rows.

        Rows are returned verbatim; deciding which states count as a failure is the
        caller's job (see ``GerritService._fetch_ci_failures``).
        """
        enc = quote(change_id_for_gerrit_rest_path(change_id), safe="")
        data = self._request_json(f"changes/{enc}/revisions/current/checks")
        if not isinstance(data, list):
            raise GerritApiError("unexpected checks response")
        return [row for row in data if isinstance(row, dict)]


def resolve_change_ref(arg: str) -> str:
    """Build a ``changes/?q=`` query string from a changeish, or pass it through unchanged.

    Query building is Gerrit dialect, so it stays here rather than in the grammar. A
    ``change:``/``cl:`` prefix is already valid Gerrit query syntax and passes through as
    written; anything the grammar does not recognize is handed to Gerrit as typed.
    """
    parsed = parse(arg)
    if parsed.kind == "query":
        return parsed.query or ""
    if parsed.kind == "triplet":
        return _triplet_query(parsed.project or "", parsed.branch or "", parsed.change_id or "")
    if parsed.kind == "change-id":
        return f"change:{parsed.raw}"
    return parsed.raw


def pick_change_from_query_result(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the single change from *rows* or raise :class:`GerritApiError` if none or ambiguous."""
    if not rows:
        raise GerritApiError("no matching change")
    if len(rows) > 1:
        nums = [str(r.get("_number", "?")) for r in rows[:5]]
        raise GerritApiError(f"ambiguous change query ({len(rows)} matches): {', '.join(nums)}")
    return rows[0]


def resolve_gerrit_web_base(settings: Settings) -> str:
    """
    Gerrit HTTPS base for the REST API and web links.

    Requires ``gerrit.webUrl`` in git config (no inference from remotes).
    """
    override = settings.gerrit_web_url
    if override:
        base = override.rstrip("/")
        logger.debug("resolve_gerrit_web_base: gerrit.webUrl -> %s", base)
        return base
    raise ConfigError(
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

# ---------------------------------------------------------------------------
# Batch change queries
# ---------------------------------------------------------------------------


def _triplet_query(project: str, branch: str, change_id: str) -> str:
    return f"project:{project} branch:{branch} change:{change_id}"


def _batch_ref_query(parsed: Changeish) -> str:
    """One-change query for a batch ref, falling back to the ref as written."""
    if parsed.kind == "triplet":
        return _triplet_query(parsed.project or "", parsed.branch or "", parsed.change_id or "")
    batch_ref = parsed.as_batch_ref()
    return f"change:{batch_ref}" if batch_ref else resolve_change_ref(parsed.raw)


def _ref_to_query(ref: str) -> str:
    return _batch_ref_query(parse(ref))


def _batch_query_limit(chunk: list[str]) -> int:
    """Allow room for the same Change-Id on multiple branches in one response."""
    return max(len(chunk) * 3 + 10, 25)


def _chunk_to_query(chunk: list[str]) -> str:
    """Build one OR query for a chunk: project-scoped Change-Id OR (no branch filter).

    Branch disambiguation is client-side via :func:`alias_batch_fetch_results`.
    When project is unknown (non-triplet refs), fall back to bare ``change:I… OR …``.
    """
    by_project: dict[str, list[str]] = {}
    seen_per_project: dict[str, set[str]] = {}
    bare_ids: list[str] = []
    seen_bare: set[str] = set()
    other_exprs: list[str] = []

    for ref in chunk:
        parsed = parse(ref)
        if parsed.kind == "triplet":
            project, change_id = parsed.project or "", parsed.change_id or ""
            seen = seen_per_project.setdefault(project, set())
            if change_id in seen:
                continue
            seen.add(change_id)
            by_project.setdefault(project, []).append(change_id)
            continue
        num = parsed.as_batch_ref()
        if num is None:
            # Not a batch ref at all. Previously this raised GerritApiError, which the caller
            # caught only to degrade the whole chunk to per-change queries.
            other_exprs.append(_batch_ref_query(parsed))
        elif num not in seen_bare:
            seen_bare.add(num)
            bare_ids.append(num)

    group_exprs: list[str] = []
    for project, change_ids in by_project.items():
        if len(change_ids) == 1:
            group_exprs.append(f"project:{project} change:{change_ids[0]}")
        else:
            inner = " OR ".join(f"change:{cid}" for cid in change_ids)
            group_exprs.append(f"project:{project} ({inner})")

    if bare_ids:
        if len(bare_ids) == 1:
            group_exprs.append(f"change:{bare_ids[0]}")
        else:
            group_exprs.append(" OR ".join(f"change:{cid}" for cid in bare_ids))

    return " OR ".join(group_exprs + other_exprs)


def _ingest_change_rows(out: dict[str, dict[str, Any]], rows: list[Any]) -> None:
    for row in rows:
        if not isinstance(row, dict):
            continue
        triplet = row.get("id")
        if isinstance(triplet, str) and triplet:
            out[triplet] = row


def alias_batch_fetch_results(
    requested: list[str],
    fetched: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Re-key batch results so each *requested* ref resolves to its ChangeInfo.

    Indexes by ``(change_id, branch)`` so duplicate Change-Ids on different
    branches never last-wins. Keeps every fetched row under its Gerrit ``id``
    (including compact ``project~number`` ids) and aliases requested
    ``project~branch~Change-Id`` triplets onto the matching branch row.
    """
    out = dict(fetched)
    by_change_branch: dict[tuple[str, str], dict[str, Any]] = {}
    by_number: dict[int, dict[str, Any]] = {}
    for payload in fetched.values():
        cid = payload.get("change_id")
        branch = payload.get("branch")
        if isinstance(cid, str) and isinstance(branch, str) and branch:
            by_change_branch[(cid, branch)] = payload
        num = payload.get("_number")
        if isinstance(num, int):
            by_number[num] = payload

    for ref in requested:
        if ref in out:
            continue
        parsed = parse(ref)
        match: dict[str, Any] | None = None
        if parsed.kind == "triplet":
            match = by_change_branch.get((parsed.change_id or "", parsed.branch or ""))
        else:
            batch_ref = parsed.as_batch_ref()
            if batch_ref is not None:
                match = by_number.get(int(batch_ref))
        if match is not None:
            out[ref] = match
    return out


def _fallback_query_chunk(client: GerritRest, chunk: list[str]) -> list[dict[str, Any]]:
    """Query each ref in *chunk* when a batched OR query fails (same session, sequential)."""
    rows: list[dict[str, Any]] = []
    for ref in chunk:
        try:
            one = query_single_change(client, ref)
        except GerritApiError as e:
            logger.warning("Gerrit query failed for %s: %s", ref, e)
            continue
        if one:
            rows.append(one)
    return rows


def _query_change_chunk(client: GerritRest, chunk: list[str], opts: list[str]) -> list[dict[str, Any]]:
    """Run one batched OR query. Empty means none exist — do not per-change fallback."""
    q = _chunk_to_query(chunk)
    try:
        return client.query_changes(q, n=_batch_query_limit(chunk), options=opts)
    except GerritApiError as e:
        logger.warning("batched Gerrit query failed (%s), falling back per change", e)
        return _fallback_query_chunk(client, chunk)


def query_single_change(client: GerritRest, ref: str) -> dict[str, Any] | None:
    """Query one Gerrit change by triplet or numeric id; raise on ambiguous multi-match."""
    try:
        rows = client.query_changes(_ref_to_query(ref), n=5, options=list(LOG_QUERY_OPTIONS))
    except GerritApiError as e:
        logger.warning("Gerrit query failed for %s: %s", ref, e)
        return None
    if not rows:
        return None
    return pick_change_from_query_result(rows)


def probe_changes_updated(client: GerritRest, refs: list[str]) -> dict[str, str]:
    """Return Gerrit ``updated`` values keyed by requested refs (and payload ids).

    Composes round trips (chunk, parallelise, alias) over :class:`GerritRest`, so it lives
    above the seam rather than on it — every implementation would otherwise have to repeat
    this. Mirrors :func:`batch_load_change_details`.
    """
    out: dict[str, str] = {}
    unique: list[str] = []
    seen: set[str] = set()
    for raw in refs:
        if raw in seen:
            continue
        seen.add(raw)
        unique.append(raw)

    def _probe_job(chunk: list[str]) -> Callable[[], list[dict[str, Any]]]:
        def _job() -> list[dict[str, Any]]:
            q = _chunk_to_query(chunk)
            return client.query_changes(q, n=_batch_query_limit(chunk), options=["SKIP_DIFFSTAT"])

        return _job

    chunks = [unique[i : i + _BATCH_OR_CHUNK] for i in range(0, len(unique), _BATCH_OR_CHUNK)]
    rows_by_chunk = parallel_map(_probe_job(chunk) for chunk in chunks)
    fetched: dict[str, dict[str, Any]] = {}
    for rows in rows_by_chunk:
        _ingest_change_rows(fetched, rows)
    aliased = alias_batch_fetch_results(unique, fetched)
    for key, payload in aliased.items():
        updated = payload.get("updated")
        if isinstance(updated, str):
            out[key] = updated
    return out


def batch_load_change_details(client: GerritRest, refs: list[str]) -> dict[str, dict[str, Any]]:
    """Map Gerrit ``id`` to ChangeInfo using project-scoped Change-Id OR chunks.

    Branch filtering is not applied in the query; callers use
    :func:`alias_batch_fetch_results` to bind requested target-branch triplets.
    """
    out: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    unique: list[str] = []
    for ref in refs:
        if ref not in seen:
            seen.add(ref)
            unique.append(ref)

    opts = list(LOG_QUERY_OPTIONS)
    chunks = [unique[i : i + _BATCH_OR_CHUNK] for i in range(0, len(unique), _BATCH_OR_CHUNK)]

    def _chunk_job(chunk: list[str]) -> Callable[[], list[dict[str, Any]]]:
        def _job() -> list[dict[str, Any]]:
            return _query_change_chunk(client, chunk, opts)

        return _job

    for rows in parallel_map(_chunk_job(chunk) for chunk in chunks):
        _ingest_change_rows(out, rows)
    return out


def batch_load_changes_by_commit(
    client: GerritRest,
    shas: list[str],
    *,
    options: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Map Gerrit ``id`` to ChangeInfo for commits, via batched ``commit:<sha> OR …``.

    Used by inbox chain assembly to resolve parent SHAs that were not in the
    original query result. Two round trips for the whole inbox: the section
    query, then this follow-up — never a per-chain relation call.
    """
    unique: list[str] = []
    seen: set[str] = set()
    for raw in shas:
        key = raw.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(key)
    if not unique:
        return {}

    opts = list(options) if options is not None else list(LOG_QUERY_OPTIONS)
    chunks = [unique[i : i + _BATCH_OR_CHUNK] for i in range(0, len(unique), _BATCH_OR_CHUNK)]
    out: dict[str, dict[str, Any]] = {}

    def _commit_job(chunk: list[str]) -> Callable[[], list[dict[str, Any]]]:
        def _job() -> list[dict[str, Any]]:
            query = " OR ".join(f"commit:{sha}" for sha in chunk)
            try:
                return client.query_changes(query, n=_batch_query_limit(chunk), options=opts)
            except GerritApiError as error:
                logger.warning("batched commit query failed (%s), falling back per sha", error)
                rows: list[dict[str, Any]] = []
                for sha in chunk:
                    try:
                        rows.extend(client.query_changes(f"commit:{sha}", n=5, options=opts))
                    except GerritApiError as inner:
                        logger.warning("Gerrit query failed for commit:%s: %s", sha, inner)
                return rows

        return _job

    for rows in parallel_map(_commit_job(chunk) for chunk in chunks):
        _ingest_change_rows(out, rows)
    return out
