"""CLI for Gerrit API cache administration."""

from __future__ import annotations

import argparse

from gerrit_workflow_tools.cli_common import (
    add_color_args,
    add_verbose_and_debug_log_args,
    init_cli_runtime,
    run_cli_command,
)
from gerrit_workflow_tools.core.gerrit.cache import GerritCache
from gerrit_workflow_tools.core.gerrit.rest import resolve_gerrit_web_base


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the command-line parser for ``ger cache``."""
    p = argparse.ArgumentParser(prog="ger cache", description="Inspect or clear the local Gerrit API cache.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info", help="Show cache path and row counts.")
    sub.add_parser("clear", help="Delete cached Gerrit API payloads for this Gerrit host.")
    add_color_args(p)
    add_verbose_and_debug_log_args(p, debug_log_help="Log cache administration diagnostics to stderr.")
    return p


def main(argv: list[str] | None = None) -> int:
    """Run ``ger cache`` administration commands."""
    return run_cli_command(lambda: _run(argv))


def _run(argv: list[str] | None) -> int:
    args = _build_parser().parse_args(argv)
    _cwd, settings, _summary_highlighter = init_cli_runtime(args)

    web_base = resolve_gerrit_web_base(settings)
    cache = GerritCache.for_web_base(web_base)
    if args.cmd == "clear":
        cache.clear()
        print(f"cleared Gerrit cache for {cache.host}: {cache.path}")
        return 0
    from gerrit_workflow_tools.core.gerrit.paths import gerrit_config_dir

    info = cache.info()
    scripts_root = gerrit_config_dir(web_base)
    print(f"host: {info.host}")
    print(f"path: {info.path}")
    print(f"scripts: {scripts_root}")
    print(f"changes: {info.changes}")
    print(f"comments: {info.comments}")
    print(f"checks: {info.checks}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
