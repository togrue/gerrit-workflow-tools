"""CLI for ``ger serve``: the local HTTP service editor integrations talk to (spec: ``docu/spec/commands/serve.md``)."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import threading
from pathlib import Path
from typing import BinaryIO

from gerrit_workflow_tools.cli_common import (
    ExitCode,
    add_verbose_and_debug_log_args,
    failure_exit,
    init_cli_runtime,
    run_cli_command,
)
from gerrit_workflow_tools.core.ci_http import CiHttp
from gerrit_workflow_tools.core.gerrit.event_source import EventSource
from gerrit_workflow_tools.core.gerrit.event_stream import EventStream, StreamState, StreamStatus
from gerrit_workflow_tools.core.gerrit.rest import GerritRest
from gerrit_workflow_tools.core.git_run import GitError
from gerrit_workflow_tools.core.git_state import repo_toplevel
from gerrit_workflow_tools.server.activity import Activity, follow_events
from gerrit_workflow_tools.server.api import PREFIX, GerApi
from gerrit_workflow_tools.server.httpd import GerHttpServer
from gerrit_workflow_tools.server.views import error_json


def _port(raw: str) -> int:
    if not raw.isdigit() or not 0 <= int(raw) <= 65535:
        raise argparse.ArgumentTypeError(f"not a port: {raw!r}")
    return int(raw)


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the command-line parser for ``ger serve``."""
    p = argparse.ArgumentParser(
        prog="ger serve",
        description=(
            "Serve this repository's review state as JSON over HTTP on 127.0.0.1, for editor integrations. "
            "Prints one JSON line with the URL and the access token, then serves until interrupted."
        ),
    )
    p.add_argument("--port", type=_port, default=0, metavar="N", help="Listen on port N (default: any free port).")
    p.add_argument(
        "--exit-with-stdin",
        action="store_true",
        help="Stop when stdin closes, so the service ends with the process that started it.",
    )
    add_verbose_and_debug_log_args(p, debug_log_help="Log requests, git and Gerrit calls to stderr.")
    return p


def _take_stdin() -> BinaryIO:
    """The starter's end of stdin, for this process alone; fd 0 becomes the null device.

    Every git child would otherwise inherit the pipe being watched. On Windows a child then
    hangs at startup, because a read is pending on that pipe; anywhere, a child could eat it.
    """
    try:
        fd = sys.stdin.fileno()
    except (AttributeError, OSError, ValueError):
        return sys.stdin.buffer  # not a real file (a test's stand-in): nothing to inherit
    watched = os.fdopen(os.dup(fd), "rb", buffering=0)
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, fd)
    os.close(null)
    return watched


def _stop_when_stdin_closes(server: GerHttpServer) -> None:
    stdin = _take_stdin()

    def watch() -> None:
        try:
            while stdin.read(4096):
                pass
        except (OSError, ValueError):
            pass
        server.shutdown()

    threading.Thread(target=watch, name="ger-serve-stdin", daemon=True).start()


def main(
    argv: list[str] | None = None,
    *,
    gerrit: GerritRest | None = None,
    ci_http: CiHttp | None = None,
    events: EventSource | None = None,
) -> int:
    """Serve until interrupted; the first stdout line is ``{"url", "token", …}``, or ``{"error": …}``.

    *events* replaces the ssh event stream (tests, the extension's end-to-end run).
    """
    return run_cli_command(lambda: _run(argv, gerrit=gerrit, ci_http=ci_http, events=events))


def _say(status: StreamStatus) -> None:
    """One line on stderr when the event stream changes; whoever started ger serve logs it."""
    print(f"ger serve: {status.sentence()}", file=sys.stderr, flush=True)


def _start(
    args: argparse.Namespace, *, gerrit: GerritRest | None, ci_http: CiHttp | None, events: EventSource | None
) -> tuple[GerHttpServer, EventStream, Path]:
    """The server, bound and ready, its event stream (not started yet), and the repository it serves.

    A known failure is also the one JSON line a client reads, in the shape a request's failure
    has, so it can tell "not in a git checkout" from a crash without reading stderr.
    """
    try:
        cwd, settings, _highlighter = init_cli_runtime(args)
        top = repo_toplevel(cwd)
        if top is None:
            raise GitError("not inside a git worktree")
        activity = Activity()
        server = GerHttpServer(
            GerApi(cwd, gerrit=gerrit, ci_http=ci_http, activity=activity),
            token=secrets.token_urlsafe(32),
            port=args.port,
        )
        stream = follow_events(
            cwd, settings, activity=activity, lock=server.api_lock, gerrit=gerrit, source=events, say=_say
        )
        if stream.status.state is StreamState.OFF and settings.event_stream != "off":
            _say(stream.status)
        return server, stream, top
    except Exception as error:  # pylint: disable=broad-exception-caught
        if failure_exit(error) is not None:
            print(json.dumps({"error": error_json(error)}), flush=True)
        raise


def _run(
    argv: list[str] | None, *, gerrit: GerritRest | None, ci_http: CiHttp | None, events: EventSource | None
) -> int:
    args = _build_parser().parse_args(argv)
    server, stream, top = _start(args, gerrit=gerrit, ci_http=ci_http, events=events)
    try:
        hello = {"url": server.url, "api": PREFIX, "token": server.token, "repo": str(top), "pid": os.getpid()}
        print(json.dumps(hello), flush=True)
        stream.start()
        if args.exit_with_stdin:
            _stop_when_stdin_closes(server)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        # The ssh child goes with the service; a client waiting on /activity is let go.
        stream.stop()
        server.api.activity.close()
        server.server_close()
    return int(ExitCode.OK)


if __name__ == "__main__":
    raise SystemExit(main())
