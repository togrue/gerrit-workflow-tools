from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from gerrit_workflow_tools.config import gerrit_password, gerrit_token, gerrit_user

logger = logging.getLogger(__name__)


class GerritApiError(RuntimeError):
    """Gerrit HTTP or JSON error."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


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


class GerritClient:
    """HTTP client for Gerrit REST ``/a/`` endpoints using git-config credentials."""

    def __init__(self, web_base: str, *, cwd: str | None = None) -> None:
        """Use *web_base* (HTTPS origin) and optional *cwd* for resolving ``gerrit.user`` / token config."""
        self.web_base = web_base.rstrip("/")
        self.cwd = cwd

    def _request_json(
        self,
        path: str,
        *,
        params: dict[str, str] | list[tuple[str, str]] | None = None,
    ) -> Any:
        if params is None:
            q = ""
        elif isinstance(params, list):
            q = f"?{urlencode(params, doseq=True)}"
        else:
            q = f"?{urlencode(params)}"
        url = f"{self.web_base}/a/{path.lstrip('/')}{q}"
        logger.info("GET %s", url)
        # headers: dict[str, str] = {"Accept": "application/json"}
        headers: dict[str, str] = {"Accept": "*/*"}
        auth = _basic_auth_header(self.cwd)
        if auth:
            headers["Authorization"] = auth
        else:
            raise GerritApiError(
                "missing Gerrit credentials in git config; set gerrit.user and "
                "gerrit.password (or gerrit.token)"
            )
        req = Request(url, headers=headers, method="GET")
        try:
            with urlopen(req, timeout=120) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except HTTPError as e:
            raise GerritApiError(
                f"Gerrit HTTP {e.code} for {url}: {e.reason}",
                status=e.code,
            ) from e
        except URLError as e:
            raise GerritApiError(f"Gerrit request failed: {e.reason!r}") from e

        try:
            parsed = json.loads(_strip_magic_json_prefix(body))
        except json.JSONDecodeError as e:
            raise GerritApiError(f"invalid JSON from Gerrit: {e}") from e
        logger.debug("response body: %s", json.dumps(parsed, indent=2))
        return parsed

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

    def get_change(self, change_id: str) -> dict[str, Any]:
        """GET change detail (labels, submittable, etc.) for *change_id*."""
        enc = quote(change_id, safe="")
        data = self._request_json(f"changes/{enc}/detail")
        if not isinstance(data, dict):
            raise GerritApiError("unexpected change detail response")
        logger.info(
            "get_change %r -> #%s %r",
            change_id,
            data.get("_number"),
            data.get("subject"),
        )
        return data

    def get_comments(self, change_id: str) -> dict[str, list[dict[str, Any]]]:
        """GET inline comments grouped by file path (or special keys) for *change_id*."""
        enc = quote(change_id, safe="")
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
            change_id,
            len(out),
            total,
        )
        return out

    def get_related(
        self, change_id: str, *, revision_id: str = "current"
    ) -> list[dict[str, Any]]:
        """GET related changes for *change_id* at *revision_id* (empty if 404)."""
        enc = quote(change_id, safe="")
        rev_enc = quote(revision_id, safe="")
        try:
            data = self._request_json(
                f"changes/{enc}/revisions/{rev_enc}/related",
            )
        except GerritApiError as e:
            if e.status == 404:
                return []
            raise
        if not isinstance(data, dict):
            return []
        ch = data.get("changes")
        if not isinstance(ch, list):
            return []
        result = [x for x in ch if isinstance(x, dict)]
        logger.info("get_related %r -> %d related change(s)", change_id, len(result))
        return result


def resolve_change_ref(arg: str) -> str:
    """Build a ``changes/`` query string for ``--change`` (numeric id, Change-Id, or passthrough)."""
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
        raise GerritApiError(
            f"ambiguous change query ({len(rows)} matches): {', '.join(nums)}"
        )
    return rows[0]
