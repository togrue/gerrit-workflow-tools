"""HTTP transport for :class:`GerApi`: loopback only, bearer token, JSON in and out, errors to status codes.

The service can check out branches and rewrite the stack, so a page in the user's browser
must not be able to reach it: every request needs the startup token, a loopback ``Host``
(DNS rebinding), and — for a body — ``application/json`` (no cross-site form posts).
"""

from __future__ import annotations

import hmac
import json
import logging
import select
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from gerrit_workflow_tools.cli_common import ExitCode, failure_exit
from gerrit_workflow_tools.core.call_trace import (
    CallTraceEntry,
    call_trace_json,
    collect_calls,
    format_call_trace_summary,
    reset_call_trace,
)
from gerrit_workflow_tools.core.review_checkout import ReviewChainEndError
from gerrit_workflow_tools.server.api import GerApi
from gerrit_workflow_tools.server.request import BadRequest, NoRoute
from gerrit_workflow_tools.server.views import error_json


logger = logging.getLogger(__name__)

LOOPBACK = "127.0.0.1"
MAX_BODY_BYTES = 1_000_000
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]"})
ACTION_HEADER = "X-Ger-Action"
"""A client's name for the user action a request serves; the answer then carries :data:`TRACE_HEADER`."""
TRACE_HEADER = "X-Ger-Trace"
"""How the request went: run and queue time, its git and Gerrit calls (``call_trace.call_trace_json``)."""

# One failure reason, one status: the shared exit code decides, as it does for the CLI.
_STATUS_BY_EXIT = {
    ExitCode.USAGE: 400,
    ExitCode.NOT_FOUND: 404,
    ExitCode.AMBIGUOUS: 409,
    ExitCode.GERRIT: 502,
    ExitCode.CI_CONTENT: 502,
    ExitCode.MISSING_CHANGE_ID: 422,
    ExitCode.DUPLICATE_CHANGE_ID: 422,
    ExitCode.NO_EDIT_ATTENTION: 404,
    ExitCode.REFUSED: 409,
}

# Failures of the transport, and the end of a review chain, which the service names apart
# from the CLI's exit: (status, code).
_OWN_FAILURES: tuple[tuple[type[BaseException], int, str], ...] = (
    (BadRequest, 400, "USAGE"),
    (NoRoute, 404, "NOT_FOUND"),
    (ReviewChainEndError, 404, "NOT_FOUND"),
)


def failure_response(error: BaseException) -> tuple[int, dict[str, Any]]:
    """HTTP status and ``{"error": {code, message}}`` body for *error*; 500 ``INTERNAL`` for a bug."""
    for kind, status, code in _OWN_FAILURES:
        if isinstance(error, kind):
            return status, {"error": {**error_json(error), "code": code}}
    failure = failure_exit(error)
    if failure is None:
        logger.error("unhandled error in ger serve", exc_info=error)
        return 500, {"error": error_json(error)}
    return _STATUS_BY_EXIT.get(failure[0], 500), {"error": error_json(error)}


def _log_timing(method: str, path: str, calls: list[CallTraceEntry], *, queued: float, took: float) -> None:
    """With ``--debug-log``: how long one request queued and ran, and its git and Gerrit calls."""
    # What a background thread recorded meanwhile is dropped: the list must not grow while serving.
    reset_call_trace()
    if not logger.isEnabledFor(logging.DEBUG):
        return
    trace = format_call_trace_summary(calls)
    logger.debug(
        "%s %s: %.0f ms, after %.0f ms queued%s",
        method,
        path,
        took * 1000,
        queued * 1000,
        f"\n{trace}" if trace else "",
    )


class GerHttpServer(ThreadingHTTPServer):
    """Serves one :class:`GerApi` on a loopback port, one request at a time."""

    daemon_threads = True

    def __init__(self, api: GerApi, *, token: str, port: int = 0) -> None:
        super().__init__((LOOPBACK, port), _Handler)
        self.api = api
        self.token = token
        self.api_lock = threading.Lock()

    @property
    def url(self) -> str:
        """The base URL clients talk to."""
        return f"http://{LOOPBACK}:{self.server_address[1]}"


class _Handler(BaseHTTPRequestHandler):
    server: GerHttpServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # pylint: disable=redefined-builtin
        logger.debug("%s %s", self.address_string(), format % args)

    def do_GET(self) -> None:  # pylint: disable=invalid-name
        """Answer a read."""
        self._answer("GET")

    def do_POST(self) -> None:  # pylint: disable=invalid-name
        """Answer an action."""
        self._answer("POST")

    def _refusal(self) -> tuple[int, str] | None:
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].lower()
        if host not in _LOOPBACK_HOSTS:
            return 403, "this service answers on loopback only"
        sent = self.headers.get("Authorization") or ""
        if not hmac.compare_digest(sent.encode(), f"Bearer {self.server.token}".encode()):
            return 401, "missing or wrong bearer token"
        return None

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length == 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise BadRequest("request body too large")
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
            raise BadRequest("the body must be application/json")
        try:
            parsed = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as error:
            raise BadRequest(f"the body is not JSON: {error}") from error
        if not isinstance(parsed, dict):
            raise BadRequest("the body must be a JSON object")
        return parsed

    def _answer(self, method: str) -> None:
        refusal = self._refusal()
        if refusal is not None:
            self._send(refusal[0], {"error": {"code": "FORBIDDEN", "message": refusal[1]}}, close=True)
            return
        asked = started = time.perf_counter()
        trace: dict[str, Any] | None = None
        action = self.headers.get(ACTION_HEADER)
        unlocked: dict[str, Any] | None = None
        with collect_calls() as calls:
            try:
                url = urlsplit(self.path)
                body = self._body() if method == "POST" else {}
                unlocked = self.server.api.handle_unlocked(method, unquote(url.path), parse_qs(url.query))
                if unlocked is not None:
                    payload = unlocked
                else:
                    with self.server.api_lock:
                        if method == "GET" and self._caller_left():
                            # Called off while it queued (the Change view moved on): nobody reads the answer.
                            self.close_connection = True
                            return
                        started = time.perf_counter()
                        payload = self.server.api.handle(method, unquote(url.path), parse_qs(url.query), body)
                status = 200
            except Exception as error:  # pylint: disable=broad-exception-caught
                status, payload = failure_response(error)
        took = time.perf_counter() - started
        if unlocked is None:
            _log_timing(method, urlsplit(self.path).path, calls, queued=started - asked, took=took)
        if action is not None:
            trace = {
                "action": action[:64],
                "ms": round(took * 1000, 1),
                "queued_ms": round((started - asked) * 1000, 1),
            }
            trace.update(call_trace_json(calls))
        self._send(status, payload, trace=trace)

    def _caller_left(self) -> bool:
        """Whether the client closed the connection: it then reads as at end, with no next request."""
        try:
            readable, _, _ = select.select([self.connection], [], [], 0)
            return bool(readable) and self.connection.recv(1, socket.MSG_PEEK) == b""
        except OSError:
            return True

    def _send(
        self, status: int, payload: dict[str, Any], *, close: bool = False, trace: dict[str, Any] | None = None
    ) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if trace is not None:
            self.send_header(TRACE_HEADER, json.dumps(trace, separators=(",", ":")))
        if close:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        self.wfile.write(data)
