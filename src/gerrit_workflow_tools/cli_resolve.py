"""CLI for ``ger resolve``: side-effect-free changeish resolution."""

from __future__ import annotations

import argparse
import json

from gerrit_workflow_tools.cli_common import (
    HELP_JSON,
    ExitCode,
    add_color_args,
    add_verbose_and_debug_log_args,
    init_cli_runtime,
    print_resolution_note,
    run_cli_command,
)
from gerrit_workflow_tools.core.gerrit.change_resolution import (
    ChangeResolutionError,
    Resolution,
    resolve_changeish,
)
from gerrit_workflow_tools.core.gerrit.rest import GerritRest
from gerrit_workflow_tools.core.gerrit.service import GerritService


_EXIT_USAGE = 2


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the command-line parser for ``ger resolve``."""
    p = argparse.ArgumentParser(
        prog="ger resolve",
        description="Resolve a changeish to a local commit and/or Gerrit change (no side effects).",
    )
    p.add_argument(
        "changeish",
        metavar="CHANGEISH",
        help=(
            "Git ref, Change-Id (I…), triplet (project~branch~I…), change: number, "
            "refs/changes/… ref, Gerrit URL, or q: search query."
        ),
    )
    p.add_argument(
        "--json",
        action="store_true",
        dest="json_",
        help=HELP_JSON,
    )
    add_color_args(p)
    add_verbose_and_debug_log_args(
        p,
        debug_log_help="Log resolution diagnostics to stderr.",
    )
    return p


def _print_text_resolution(resolution: Resolution) -> None:
    if resolution.local_sha:
        print(f"local SHA: {resolution.local_sha}")
    if resolution.selected is not None:
        sel = resolution.selected
        print(f"Gerrit change: #{sel.number} {sel.triplet} (branch {sel.branch}, status {sel.status})")
    elif resolution.local_sha is None:
        print("(no local commit or Gerrit change resolved)")


def main(argv: list[str] | None = None, *, gerrit: GerritRest | None = None) -> int:
    """Resolve *changeish* and print human-readable or JSON resolution details."""
    return run_cli_command(lambda: _run(argv, gerrit=gerrit))


def _run(argv: list[str] | None, *, gerrit: GerritRest | None) -> int:
    p = _build_parser()
    args = p.parse_args(argv)
    cwd, settings, _summary_highlighter = init_cli_runtime(args)

    service = GerritService.from_cwd(cwd, settings=settings, rest=gerrit)
    resolution = resolve_changeish(
        args.changeish,
        client=service.rest,
        cwd=cwd,
        settings=settings,
        explicit_target=True,
    )
    if resolution.selected is None and resolution.kind == "change-id":
        raise ChangeResolutionError(f"Gerrit change not found for {args.changeish!r}")

    print_resolution_note(resolution)

    if args.json_:
        print(json.dumps({"resolution": resolution.to_json_dict()}, indent=2))
        return int(ExitCode.OK)

    _print_text_resolution(resolution)
    return int(ExitCode.OK)


if __name__ == "__main__":
    raise SystemExit(main())
