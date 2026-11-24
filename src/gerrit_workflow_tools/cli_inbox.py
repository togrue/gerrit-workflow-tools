"""CLI for ``ger inbox``: open review chains waiting on you (*to review*) and your own (*outgoing*)."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import datetime, timezone

from gerrit_workflow_tools.cli_common import (
    ExitCode,
    add_color_args,
    add_output_format_args,
    add_verbose_and_debug_log_args,
    init_cli_runtime,
    output_format,
    run_cli_command,
)
from gerrit_workflow_tools.core.gerrit.rest import GerritRest
from gerrit_workflow_tools.core.inbox import OUTGOING, TO_REVIEW, load_inbox, project_scope
from gerrit_workflow_tools.core.review_chain import host_from_web_base
from gerrit_workflow_tools.render.ai_text import inbox_ai_lines
from gerrit_workflow_tools.render.inbox_text import render_inbox_text
from gerrit_workflow_tools.render.review_json import iso_utc, sections_json
from gerrit_workflow_tools.summary_highlight import run_summary_highlighter


def _limit_value(raw: str) -> int:
    if not raw.isdigit() or int(raw) < 1:
        raise argparse.ArgumentTypeError(f"must be a positive number: {raw!r}")
    return int(raw)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ger inbox",
        description=(
            "Open Gerrit review chains: those waiting on your review (to review) and your own "
            "(outgoing). Inside a clone, only that clone's project; --all for every project."
        ),
    )
    section = parser.add_mutually_exclusive_group()
    section.add_argument(
        "--to-review",
        action="store_const",
        const=TO_REVIEW,
        dest="section",
        help="Only the to-review section: chains waiting on your review.",
    )
    section.add_argument(
        "--outgoing",
        action="store_const",
        const=OUTGOING,
        dest="section",
        help="Only the outgoing section: your own open chains.",
    )
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument(
        "--project",
        action="append",
        default=[],
        metavar="P",
        help="Restrict to a Gerrit project. Repeatable. Default: the current clone's project.",
    )
    scope.add_argument(
        "--all",
        action="store_true",
        dest="all_projects",
        help="Every project on the host, not only the current clone's.",
    )
    parser.add_argument(
        "--unready",
        action="store_true",
        dest="include_unready",
        help="Include to-review chains filtered out as not ready (WIP, private, CI red).",
    )
    parser.add_argument(
        "--limit",
        type=_limit_value,
        default=None,
        metavar="N",
        help="At most N chains per section.",
    )
    add_output_format_args(parser, ai_view="one plain line per chain")
    parser.add_argument(
        "--url",
        "--show-url",
        action="store_true",
        dest="url",
        default=True,
        help="Print the Gerrit URL of each chain top (default: on).",
    )
    parser.add_argument(
        "--no-url",
        action="store_false",
        dest="url",
        help="Omit Gerrit URLs from text output.",
    )
    add_color_args(parser)
    add_verbose_and_debug_log_args(
        parser,
        verbose_help="List every outgoing chain and every chain member, not only those that need you.",
    )
    return parser


def main(argv: list[str] | None = None, *, gerrit: GerritRest | None = None) -> int:
    """CLI entry for ``ger inbox``."""
    return run_cli_command(lambda: _run(argv, gerrit=gerrit))


def _run(argv: list[str] | None, *, gerrit: GerritRest | None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    run = init_cli_runtime(args, repository=False, gerrit=gerrit)
    highlighter = run_summary_highlighter(run)
    cwd, settings = run.cwd, run.settings
    service = run.gerrit
    projects = project_scope(cwd, settings, projects=list(args.project), all_projects=bool(args.all_projects))
    now = datetime.now(timezone.utc)
    limit = args.limit if args.limit is not None else settings.inbox_limit
    sections = load_inbox(
        service,
        now=now,
        projects=projects,
        all_projects=not projects,
        include_unready=bool(args.include_unready),
        limit=limit,
        sections=(TO_REVIEW, OUTGOING) if args.section is None else (args.section,),
        count_not_ready=output_format(args) == "human",
        with_drafts=output_format(args) == "human",
    )
    if limit is not None:
        sections = [replace(s, chains=s.chains[: max(0, limit)]) for s in sections]
    out_fmt = output_format(args)
    if out_fmt == "ai":
        print("\n".join(inbox_ai_lines(sections, projects=projects)))
    elif out_fmt == "json":
        payload = {
            "host": host_from_web_base(service.web_base),
            "generated": iso_utc(now),
            "projects": projects,
            "sections": sections_json(sections),
        }
        print(json.dumps(payload, indent=2))
    else:
        render_inbox_text(
            sections,
            projects=projects,
            highlighter=highlighter,
            show_url=bool(args.url),
            verbose=bool(args.verbose),
        )
    return int(ExitCode.OK)


if __name__ == "__main__":
    raise SystemExit(main())
