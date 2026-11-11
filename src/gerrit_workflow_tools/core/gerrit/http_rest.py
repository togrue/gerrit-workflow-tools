"""``HttpGerritRest``: the :class:`GerritRest` seam over Gerrit's HTTP REST API."""

from __future__ import annotations

import base64
import json
import logging
import socket
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from gerrit_workflow_tools.core.call_trace import gerrit_request_label_from_url, record_call
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.rest import (
    LOG_QUERY_OPTIONS,
    GerritApiError,
    GerritUnavailableError,
    change_id_for_gerrit_rest_path,
    encode_gerrit_path_segment,
    resolve_gerrit_web_base,
)


logger = logging.getLogger(__name__)
_LOG_RESPONSE_BODIES = False


def set_log_gerrit_response_bodies(enabled: bool) -> None:
    """Configure whether full Gerrit JSON payloads should be debug-logged."""

    global _LOG_RESPONSE_BODIES  # pylint: disable=global-statement
    _LOG_RESPONSE_BODIES = enabled


def _change_path(change_id: str, suffix: str = "") -> str:
    """``changes/<id><suffix>``, with the id in the form and encoding Gerrit expects."""
    return f"changes/{encode_gerrit_path_segment(change_id_for_gerrit_rest_path(change_id))}{suffix}"


def _draft_path(change_id: str, revision: int | str, draft_id: str | None) -> str:
    """``changes/<id>/revisions/<rev>/drafts[/<draft-id>]``; a patchset number is a valid revision id."""
    suffix = f"/revisions/{encode_gerrit_path_segment(revision)}/drafts"
    return _change_path(change_id, f"{suffix}/{encode_gerrit_path_segment(draft_id)}" if draft_id else suffix)


def _file_map(data: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """A CommentInfo map by file path, keeping only well-formed rows."""
    return {path: [c for c in rows if isinstance(c, dict)] for path, rows in data.items() if isinstance(rows, list)}


def _strip_magic_json_prefix(raw: str) -> str:
    s = raw.lstrip()
    if s.startswith(")]}'"):
        nl = s.find("\n")
        if nl != -1:
            return s[nl + 1 :]
    return raw


def _why_unreachable(error: OSError) -> str:
    """A few words on why a connection failed, without the platform's localized prose."""
    reason = error.reason if isinstance(error, URLError) else error
    if isinstance(reason, ConnectionRefusedError):
        return "connection refused"
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return "timed out"
    if isinstance(reason, socket.gaierror):
        return "unknown host"
    if isinstance(reason, OSError) and reason.strerror:
        return reason.strerror
    return str(reason)


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


class HttpGerritRest:
    """HTTP client for Gerrit REST ``/a/`` endpoints using git-config credentials."""

    def __init__(self, web_base: str, *, auth: GerritAuth | None = None) -> None:
        """Use *web_base* (HTTPS origin) with credentials already resolved.

        Credentials are a construction-time input, not something read from git config per
        request — that is what keeps ``cwd`` off :class:`GerritRest`.
        """
        self.web_base = web_base.rstrip("/")
        self.auth = auth

    @property
    def host(self) -> str:
        """``host[:port]`` of :attr:`web_base`, which is how a failure names the server."""
        return urlsplit(self.web_base).netloc or self.web_base

    @classmethod
    def from_settings(cls, settings: Settings, *, web_base: str | None = None) -> HttpGerritRest:
        """Build a client with credentials taken from *settings*.

        *web_base* defaults to ``gerrit.webUrl`` (:func:`resolve_gerrit_web_base`, which
        raises :class:`ConfigError` when it is unset).
        """
        base = web_base if web_base is not None else resolve_gerrit_web_base(settings)
        return cls(base, auth=gerrit_auth_from_settings(settings))

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
            if e.code == 401:
                raise GerritUnavailableError(
                    f"Gerrit at {self.host} rejected the credentials (HTTP 401); "
                    "check gerrit.user and gerrit.token, or run `ger setup`",
                    status=e.code,
                ) from e
            raise GerritApiError(
                f"Gerrit HTTP {e.code} for {url}: {e.reason}",
                status=e.code,
            ) from e
        except (URLError, OSError) as e:
            raise GerritUnavailableError(f"Gerrit unreachable at {self.host} ({_why_unreachable(e)})") from e
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
        return _file_map(self._request_dict("comments", _change_path(change_id, "/comments"), params=params))

    def list_drafts(self, change_id: str) -> dict[str, list[dict[str, Any]]]:
        """GET ``changes/<id>/drafts``: the caller's drafts on every patchset, grouped by file path."""
        return _file_map(self._request_dict("drafts", _change_path(change_id, "/drafts")))

    def put_draft(
        self, change_id: str, revision: int | str, comment: dict[str, Any], *, draft_id: str | None = None
    ) -> dict[str, Any]:
        """PUT ``…/revisions/<rev>/drafts`` (create) or ``…/drafts/<id>`` (update)."""
        return self._request_dict("draft", _draft_path(change_id, revision, draft_id), method="PUT", json_body=comment)

    def delete_draft(self, change_id: str, revision: int | str, draft_id: str) -> None:
        """DELETE ``…/revisions/<rev>/drafts/<id>``."""
        self._request_json(_draft_path(change_id, revision, draft_id), method="DELETE")

    def publish_drafts(
        self, change_id: str, *, message: str | None = None, labels: Mapping[str, int] | None = None
    ) -> dict[str, Any]:
        """POST a review that publishes the caller's drafts on all revisions, as the web UI's Send."""
        review: dict[str, Any] = {"drafts": "PUBLISH_ALL_REVISIONS"}
        if message:
            review["message"] = message
        if labels:
            review.update(labels=dict(labels))
        path = _change_path(change_id, "/revisions/current/review")
        return self._request_dict("publish drafts", path, method="POST", json_body=review)

    def get_related(self, change_id: str, revision: int | str = "current") -> dict[str, Any]:
        """GET ``changes/<id>/revisions/<rev>/related``."""
        return self._request_dict("related changes", _change_path(change_id, f"/revisions/{revision}/related"))

    def get_checks(self, change_id: str) -> list[dict[str, Any]]:
        """GET ``changes/<id>/revisions/current/checks`` (Checks plugin) and return raw rows.

        Rows are returned verbatim; deciding which states count as a failure is the
        caller's job (see ``GerritService._fetch_ci_result``).
        """
        return self._request_rows("checks", _change_path(change_id, "/revisions/current/checks"))

    def get_messages(self, change_id: str) -> list[dict[str, Any]]:
        """GET ``changes/<id>/messages`` and return raw ChangeMessageInfo rows."""
        return self._request_rows("messages", _change_path(change_id, "/messages"))
