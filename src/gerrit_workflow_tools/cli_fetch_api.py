"""CLI for ``ger fetch-api``: GET arbitrary Gerrit REST paths with configured credentials."""

from __future__ import annotations

import argparse
import json
import sys
from urllib.parse import quote, urlsplit

from gerrit_workflow_tools.cli_common import ExitCode, add_verbose_and_debug_log_args, init_cli_runtime, run_cli_command
from gerrit_workflow_tools.core.gerrit.http_rest import HttpGerritRest


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the command-line parser for ``ger fetch-api``."""
    p = argparse.ArgumentParser(
        prog="ger fetch-api",
        description=(
            "GET a Gerrit REST path under /a/ using gerrit.webUrl and HTTP Basic auth "
            "(gerrit.user with gerrit.token or gerrit.password)."
        ),
    )
    p.add_argument(
        "path",
        metavar="PATH",
        help="Path under the authenticated API, e.g. changes/12345/detail or accounts/self/detail.",
    )
    p.add_argument(
        "--compact",
        action="store_true",
        help="Write a single line of JSON to stdout.",
    )
    add_verbose_and_debug_log_args(p, debug_log_help="Log resolved URL and Gerrit API diagnostics to stderr.")
    return p


def _api_path(raw: str) -> str | None:
    """*raw* made safe to append to ``/a/``, or ``None`` when it would leave the API."""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return None
    if parts.scheme or parts.netloc or ".." in parts.path.split("/"):
        return None
    return quote(raw.lstrip("/"), safe="/?&=:+~%,@!$'()*;")


def main(argv: list[str] | None = None) -> int:
    """CLI entry for ``ger fetch-api``: GET a path under ``/a/`` and print JSON."""
    return run_cli_command(lambda: _run(argv))


def _run(argv: list[str] | None) -> int:
    args = _build_parser().parse_args(argv)
    _cwd, settings, _summary_highlighter = init_cli_runtime(args)
    path = _api_path(args.path)
    if path is None:
        print(
            f"error: invalid path {args.path!r}: expected a path under /a/, such as changes/104. "
            "Git Bash rewrites a leading /, so leave it off.",
            file=sys.stderr,
        )
        return int(ExitCode.USAGE)
    client = HttpGerritRest.from_settings(settings)
    data = client.get_json(path)
    indent = None if args.compact else 2
    print(json.dumps(data, indent=indent, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
