"""CLI for ``ger fetch-api``: GET arbitrary Gerrit REST paths with configured credentials."""

from __future__ import annotations

import argparse
import json
import sys

from gerrit_workflow_tools.cli_common import add_verbose_and_debug_log_args, configure_logging, cwd_from_env
from gerrit_workflow_tools.core.gerrit_client import GerritApiError, GerritClient, resolve_gerrit_web_base


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


def main(argv: list[str] | None = None) -> int:
    """CLI entry for ``ger fetch-api``: GET a path under ``/a/`` and print JSON."""
    p = _build_parser()
    args = p.parse_args(argv)
    configure_logging(args.debug_log)
    cwd = cwd_from_env()

    try:
        web_base = resolve_gerrit_web_base(cwd)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    client = GerritClient(web_base, cwd=str(cwd))
    try:
        data = client.get_json(args.path)
    except GerritApiError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    indent = None if args.compact else 2
    print(json.dumps(data, indent=indent, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
