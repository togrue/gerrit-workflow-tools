"""HTTP client wrapper for Gerrit REST API calls."""

from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, TypeVar

from gerrit_workflow_tools.core.config import gerrit_password, gerrit_token, gerrit_user, gerrit_web_url
from gerrit_workflow_tools.core.git_run import GitError

logger = logging.getLogger(__name__)
_LOG_RESPONSE_BODIES = False

_MAX_PARALLEL_IO = 8
T = TypeVar("T")
R = TypeVar("R")


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


def change_id_for_gerrit_rest_path(change_id: str) -> str:
    """
    Return *change_id* for Gerrit ``changes/<id>/…`` URL segments.

    Gerrit expects the canonical Change-Id with an uppercase ``I`` prefix; values
    taken from :func:`~gerrit_workflow_tools.core.gerrit_change_status.norm_change_id`
    use a lowercase ``i`` and yield HTTP 404 unless corrected.
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


def _basic_auth(cwd: str | None) -> Any:
    from requests.auth import HTTPBasicAuth

    user = gerrit_user(cwd)
    secret = gerrit_token(cwd) or gerrit_password(cwd)
    if not user or secret is None:
        raise GerritApiError(
            "missing Gerrit credentials in git config; set gerrit.user and gerrit.password (or gerrit.token)"
        )
    return HTTPBasicAuth(user, secret)


def parallel_io_workers(task_count: int, *, min_tasks_per_worker: int = 1) -> int:
    """Balance parallelism against per-worker HTTP session overhead."""

    if task_count <= 1:
        return 1
    per_worker = max(1, min_tasks_per_worker)
    capped = min(_MAX_PARALLEL_IO, (task_count + per_worker - 1) // per_worker)
    return max(1, min(task_count, capped))


def partition_tasks(items: list[T], workers: int) -> list[list[T]]:
    """Split *items* into *workers* buckets as evenly as possible."""

    if not items:
        return []
    bucket_count = min(workers, len(items))
    buckets: list[list[T]] = [[] for _ in range(bucket_count)]
    for index, item in enumerate(items):
        buckets[index % bucket_count].append(item)
    return buckets


def parallel_map(
    client: GerritClient,
    items: list[T],
    fn: Callable[[GerritClient, T], R],
    *,
    min_tasks_per_worker: int = 1,
) -> list[R]:
    """Run *fn* over *items* with one persistent session per worker thread.

    Return values are in the same order as *items* (unlike naive bucket merge).
    """

    if not items:
        return []
    if len(items) == 1:
        return [fn(client, items[0])]

    workers = parallel_io_workers(len(items), min_tasks_per_worker=min_tasks_per_worker)
    indexed: list[tuple[int, T]] = list(enumerate(items))
    buckets = partition_tasks(indexed, workers)

    def run_bucket(batch: list[tuple[int, T]]) -> list[tuple[int, R]]:
        return [(i, fn(client, item)) for i, item in batch]

    with ThreadPoolExecutor(max_workers=len(buckets)) as ex:
        parts = list(ex.map(run_bucket, buckets))
    unset = object()
    out: list[Any] = [unset] * len(items)
    for part in parts:
        for i, value in part:
            out[i] = value
    if any(v is unset for v in out):
        raise RuntimeError("parallel_map: worker did not return a result for every item")
    return out  # type: ignore[return-value]


class GerritClient:
    """HTTP client for Gerrit REST ``/a/`` endpoints using git-config credentials."""

    def __init__(self, web_base: str, *, cwd: str | None = None) -> None:
        """Use *web_base* (HTTPS origin) and optional *cwd* for resolving ``gerrit.user`` / token config."""
        self.web_base = web_base.rstrip("/")
        self.cwd = cwd
        self._auth: Any | None = None
        self._tls = threading.local()

    def _resolve_auth(self) -> Any:
        if self._auth is None:
            self._auth = _basic_auth(self.cwd)
        return self._auth

    def _session(self) -> Any:
        import requests
        from requests.adapters import HTTPAdapter

        session = getattr(self._tls, "session", None)
        if session is None:
            session = requests.Session()
            session.auth = self._resolve_auth()
            session.headers.update({"Accept": "*/*", "Connection": "keep-alive"})
            adapter = HTTPAdapter(pool_connections=1, pool_maxsize=4, max_retries=0)
            session.mount("https://", adapter)
            session.mount("http://", adapter)
            self._tls.session = session
        return session

    def close(self) -> None:
        """Close the current thread's HTTP session, if any."""

        session = getattr(self._tls, "session", None)
        if session is not None:
            session.close()
            self._tls.session = None

    def __enter__(self) -> GerritClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _url(self, path: str, *, params: dict[str, str] | list[tuple[str, str]] | None) -> str:
        from urllib.parse import urlencode

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
        headers: dict[str, str] = {}
        if json_body is not None:
            headers["Content-Type"] = "application/json; charset=UTF-8"
        logger.info("%s %s", method, url)
        try:
            resp = self._session().request(
                method,
                url,
                headers=headers or None,
                json=json_body,
                timeout=120,
            )
        except Exception as e:
            import requests

            if isinstance(e, requests.RequestException):
                raise GerritApiError(f"Gerrit request failed: {e!r}") from e
            raise

        if resp.status_code >= 400:
            raise GerritApiError(
                f"Gerrit HTTP {resp.status_code} for {url}: {resp.reason}",
                status=resp.status_code,
            )

        raw = resp.text
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

    def suggest_change_reviewers(
        self,
        change_id: str,
        *,
        query: str | None = None,
        n: int = 20,
    ) -> list[dict[str, Any]]:
        """GET suggested reviewers for ``change_id`` via ``changes/<id>/suggest_reviewers``."""
        from urllib.parse import quote

        enc = quote(change_id, safe="")
        params: list[tuple[str, str]] = [("n", str(n))]
        if query:
            params.insert(0, ("q", query))
        data = self._request_json(f"changes/{enc}/suggest_reviewers", params=params)
        if not isinstance(data, list):
            raise GerritApiError("unexpected suggest reviewers response")
        out = [row for row in data if isinstance(row, dict)]
        logger.info("suggest_change_reviewers %r -> %d result(s)", change_id, len(out))
        return out

    def get_plugin_project_reviewers(self, project: str) -> list[dict[str, Any]] | None:
        """GET project-level reviewer defaults from reviewers plugin (if installed)."""
        from urllib.parse import quote

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
        from urllib.parse import quote

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
        from urllib.parse import quote

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
        from urllib.parse import quote

        cid = change_id_for_gerrit_rest_path(change_id)
        enc = quote(cid, safe="")
        data = self._request_json(
            f"changes/{enc}/revisions/current/review",
            method="POST",
            json_body={"reviewers": [{"reviewer": reviewer}]},
        )
        if not isinstance(data, dict):
            raise GerritApiError("unexpected add reviewer response")
        logger.info("add_reviewer %r -> %s", cid, data.get("_account_id"))
        return data

    def delete_reviewer(self, change_id: str, account_id: int) -> Any:
        """Remove *account_id* from *change_id* (REVIEWER or CC)."""
        from urllib.parse import quote

        cid = change_id_for_gerrit_rest_path(change_id)
        enc = quote(cid, safe="")
        aid_enc = quote(str(account_id), safe="")
        return self._request_json(f"changes/{enc}/reviewers/{aid_enc}", method="DELETE")

    def get_comments(self, change_id: str) -> dict[str, list[dict[str, Any]]]:
        """GET inline comments grouped by file path (or special keys) for *change_id*."""
        from urllib.parse import quote

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


_RESOLVE_CHANGE_QUERY_OPTIONS = (
    "DETAILED_LABELS",
    "SUBMITTABLE",
    "CURRENT_REVISION",
    "ALL_REVISIONS",
)


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
