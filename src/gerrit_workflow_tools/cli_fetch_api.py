"""CLI for ``ger fetch-api``: GET arbitrary Gerrit REST paths with configured credentials."""

from __future__ import annotations

import argparse
import json

from gerrit_workflow_tools.cli_common import add_verbose_and_debug_log_args, init_cli_runtime, run_cli_command
from gerrit_workflow_tools.core.gerrit.rest import HttpGerritRest, resolve_gerrit_web_base


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
    return run_cli_command(lambda: _run(argv))


def _run(argv: list[str] | None) -> int:
    args = _build_parser().parse_args(argv)
    _cwd, settings, _summary_highlighter = init_cli_runtime(args)
    client = HttpGerritRest.from_settings(resolve_gerrit_web_base(settings), settings)
    data = client.get_json(args.path)
    indent = None if args.compact else 2
    print(json.dumps(data, indent=indent, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
