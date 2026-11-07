"""Low-level Gerrit REST API access."""

from __future__ import annotations

import base64
import json
import logging
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlencode
from urllib.request import Request, urlopen

from gerrit_workflow_tools.core.call_trace import gerrit_request_label_from_url, record_call
from gerrit_workflow_tools.core.changeish import is_change_id
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


def encode_gerrit_path_segment(segment: str | int) -> str:
    """URL-encode one Gerrit REST path segment (project, change id, account, …).

    ``ChangeInfo.id`` values are already percent-encoded (``tools%2Frepo~123``).
    Unquote first so raw locally-built triplets and Gerrit-returned ids both
    encode once — never to ``%252F``.
    """

    return quote(unquote(str(segment)), safe="")


def _change_path(change_id: str, suffix: str = "") -> str:
    """``changes/<id><suffix>``, with the id in the form and encoding Gerrit expects."""
    return f"changes/{encode_gerrit_path_segment(change_id_for_gerrit_rest_path(change_id))}{suffix}"


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
        """Return ChangeInfo rows; optional fields (labels, revisions, …) only as *options* ask."""

    def query_accounts(self, query: str, *, n: int = 10) -> list[dict[str, Any]]:
        """Return AccountInfo rows matching an account search query."""

    def get_change(self, change_id: str) -> dict[str, Any]:
        """Return one ChangeInfo, carrying the fields of a :data:`LOG_QUERY_OPTIONS` query row."""

    def get_account(self, account_id: int | str) -> dict[str, Any]:
        """Return AccountInfo detail for one account."""

    def get_comments(
        self,
        change_id: str,
        *,
        enable_context: bool = False,
        context_padding: int | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Return inline comments grouped by file path."""

    def get_checks(self, change_id: str) -> list[dict[str, Any]]:
        """Return Checks-plugin rows for the current revision."""

    def get_messages(self, change_id: str) -> list[dict[str, Any]]:
        """Return change message rows (review/robot messages) for one change."""

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
        label = gerrit_request_label_from_url(method, url)
        t0 = time.perf_counter()
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
        finally:
            record_call("gerrit", label, time.perf_counter() - t0)

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

    def _request_dict(self, what: str, path: str, **request: Any) -> dict[str, Any]:
        """One request whose answer must be a JSON object; *what* names it in the error."""
        data = self._request_json(path, **request)
        if not isinstance(data, dict):
            raise GerritApiError(f"unexpected {what} response")
        return data

    def _request_rows(self, what: str, path: str, **request: Any) -> list[dict[str, Any]]:
        """One request whose answer must be a JSON list; returns its object rows."""
        data = self._request_json(path, **request)
        if not isinstance(data, list):
            raise GerritApiError(f"unexpected {what} response")
        rows = [row for row in data if isinstance(row, dict)]
        logger.info("%s -> %d row(s)", what, len(rows))
        return rows

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
        params = [("q", query), ("n", str(n)), *(("o", opt) for opt in options or ())]
        return self._request_rows("changes query", "changes/", params=params)

    def query_accounts(self, query: str, *, n: int = 10) -> list[dict[str, Any]]:
        """GET ``accounts/?q=...`` and return account rows."""
        return self._request_rows("accounts query", "accounts/", params=[("q", query), ("n", str(n))])

    def get_account(self, account_id: int | str) -> dict[str, Any]:
        """GET account detail by Gerrit account id, username, email, or ``self``."""
        return self._request_dict("account detail", f"accounts/{encode_gerrit_path_segment(account_id)}/detail")

    def suggest_change_reviewers(
        self,
        change_id: str,
        *,
        query: str | None = None,
        n: int = 20,
    ) -> list[dict[str, Any]]:
        """GET suggested reviewers for ``change_id`` via ``changes/<id>/suggest_reviewers``."""
        params = [*((("q", query),) if query else ()), ("n", str(n))]
        return self._request_rows("suggest reviewers", _change_path(change_id, "/suggest_reviewers"), params=params)

    def get_plugin_project_reviewers(self, project: str) -> list[dict[str, Any]] | None:
        """GET project-level reviewer defaults from reviewers plugin (if installed)."""
        try:
            return self._request_rows("project reviewers", f"projects/{encode_gerrit_path_segment(project)}/reviewers")
        except GerritApiError as e:
            if e.status == 404:
                return None
            raise

    def get_change(self, change_id: str) -> dict[str, Any]:
        """GET one ChangeInfo shaped like a batch row (not ``/detail``: no ``current_revision``)."""
        return self._request_dict(
            "change detail", _change_path(change_id), params=[("o", o) for o in LOG_QUERY_OPTIONS]
        )

    def list_change_reviewers(self, change_id: str) -> list[dict[str, Any]]:
        """GET ``changes/<id>/reviewers/`` (lighter than full ``/detail``)."""
        return self._request_rows("list reviewers", _change_path(change_id, "/reviewers/"))

    def set_reviewers_batch(
        self,
        change_id: str,
        *,
        reviewers: list[str] | None = None,
        ccs: list[str] | None = None,
    ) -> dict[str, Any]:
        """POST multiple reviewers/CCs onto *change_id* in one review request."""
        reviewer_inputs: list[dict[str, str | bool]] = []
        reviewer_inputs.extend({"reviewer": reviewer, "confirmed": True} for reviewer in reviewers or [])
        reviewer_inputs.extend({"reviewer": cc, "state": "CC", "confirmed": True} for cc in ccs or [])
        if not reviewer_inputs:
            return {}
        return self._request_dict(
            "set reviewers",
            _change_path(change_id, "/revisions/current/review"),
            method="POST",
            json_body={"reviewers": reviewer_inputs},
        )

    def delete_reviewer(self, change_id: str, account_id: int) -> Any:
        """Remove *account_id* from *change_id* (REVIEWER or CC)."""
        path = _change_path(change_id, f"/reviewers/{encode_gerrit_path_segment(account_id)}")
        return self._request_json(path, method="DELETE")

    def set_topic(self, change_id: str, topic: str | None) -> None:
        """Set or clear the topic on *change_id*.

        Gerrit ``PUT /changes/{id}/topic`` returns the new topic as a bare JSON
        string (e.g. ``"my-topic"``), not a JSON object.  Clearing the topic
        yields ``204 No Content`` (mapped to ``{}`` by ``_request_json``).
        Both are valid; neither needs further validation.
        """
        self._request_json(_change_path(change_id, "/topic"), method="PUT", json_body={"topic": topic or ""})

    def set_wip(self, change_id: str, on: bool) -> dict[str, Any]:
        """Mark *change_id* work-in-progress when *on*, otherwise ready for review."""
        path = _change_path(change_id, "/wip" if on else "/ready")
        return self._request_dict("set WIP", path, method="POST", json_body={})

    def set_private(self, change_id: str, on: bool) -> dict[str, Any]:
        """Mark *change_id* private when *on*, otherwise remove the private flag."""
        path = _change_path(change_id, "/private")
        if on:
            return self._request_dict("set private", path, method="POST", json_body={})
        return self._request_dict("set private", path, method="DELETE")

    def get_comments(
        self,
        change_id: str,
        *,
        enable_context: bool = False,
        context_padding: int | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """GET inline comments grouped by file path (or special keys) for *change_id*."""
        params: dict[str, str] | None = None
        if enable_context:
            params = {"enable-context": "true"}
            if context_padding is not None:
                params["context-padding"] = str(context_padding)
        data = self._request_dict("comments", _change_path(change_id, "/comments"), params=params)
        return {path: [c for c in rows if isinstance(c, dict)] for path, rows in data.items() if isinstance(rows, list)}

    def get_checks(self, change_id: str) -> list[dict[str, Any]]:
        """GET ``changes/<id>/revisions/current/checks`` (Checks plugin) and return raw rows.

        Rows are returned verbatim; deciding which states count as a failure is the
        caller's job (see ``GerritService._fetch_ci_result``).
        """
        return self._request_rows("checks", _change_path(change_id, "/revisions/current/checks"))

    def get_messages(self, change_id: str) -> list[dict[str, Any]]:
        """GET ``changes/<id>/messages`` and return raw ChangeMessageInfo rows."""
        return self._request_rows("messages", _change_path(change_id, "/messages"))


def pick_change_from_query_result(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the single change from *rows* or raise :class:`GerritApiError` if none or ambiguous."""
    if not rows:
        raise GerritApiError("no matching change")
    if len(rows) > 1:
        shown = ", ".join(str(r.get("_number", "?")) for r in rows[:5]) + (", ..." if len(rows) > 5 else "")
        more = "+" if rows[-1].get("_more_changes") else ""
        raise GerritApiError(f"ambiguous change query ({len(rows)}{more} matches): {shown}")
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
    "DETAILED_ACCOUNTS",
    "SUBMITTABLE",
    "CURRENT_REVISION",
    "ALL_REVISIONS",
)
