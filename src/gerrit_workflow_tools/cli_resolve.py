"""CLI for ``ger resolve``: side-effect-free changeish resolution."""

from __future__ import annotations

import argparse
import json
import sys

from gerrit_workflow_tools.cli_common import (
    EXIT_AMBIGUOUS,
    EXIT_RESOLUTION_ERROR,
    HELP_JSON,
    add_color_args,
    add_verbose_and_debug_log_args,
    init_cli_runtime,
)
from gerrit_workflow_tools.cli_style import ANSI_DIM, color_text
from gerrit_workflow_tools.core.gerrit.change_resolution import (
    ChangeAmbiguousError,
    ChangeResolutionError,
    Resolution,
    format_resolution_note,
    resolve_changeish,
)
from gerrit_workflow_tools.core.gerrit.rest import GerritApiError, GerritRest
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


def _print_resolution_note(resolution_note: str | None, *, use_color: bool) -> None:
    if not resolution_note:
        return
    text = color_text(resolution_note, ANSI_DIM) if use_color else resolution_note
    print(text, file=sys.stderr)


def _print_text_resolution(resolution: Resolution) -> None:
    if resolution.local_sha:
        print(f"local SHA: {resolution.local_sha}")
    if resolution.selected is not None:
        sel = resolution.selected
        print(
            f"Gerrit change: #{sel.number} {sel.triplet} "
            f"(branch {sel.branch}, status {sel.status})"
        )
    elif resolution.local_sha is None:
        print("(no local commit or Gerrit change resolved)")


def main(argv: list[str] | None = None, *, gerrit: GerritRest | None = None) -> int:
    """Resolve *changeish* and print human-readable or JSON resolution details."""
    p = _build_parser()
    args = p.parse_args(argv)
    cwd, _summary_highlighter = init_cli_runtime(debug_log=args.debug_log, color=args.color)
    use_color = args.color != "never"

    try:
        service = GerritService.from_cwd(cwd, rest=gerrit)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_RESOLUTION_ERROR

    try:
        resolution = resolve_changeish(
            args.changeish,
            client=service.rest,
            cwd=cwd,
            explicit_target=True,
        )
    except ChangeAmbiguousError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_AMBIGUOUS
    except ChangeResolutionError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_RESOLUTION_ERROR
    except GerritApiError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_RESOLUTION_ERROR

    if resolution.selected is None and resolution.kind == "change-id":
        print(f"error: Gerrit change not found for {args.changeish!r}", file=sys.stderr)
        return EXIT_RESOLUTION_ERROR

    _print_resolution_note(format_resolution_note(resolution), use_color=use_color)

    if args.json_:
        print(json.dumps({"resolution": resolution.to_json_dict()}, indent=2))
        return 0

    _print_text_resolution(resolution)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
