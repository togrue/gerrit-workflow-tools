"""CLI for ``ger inbox``: open review chains waiting on you (*to review*) and your own (*outgoing*)."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from gerrit_workflow_tools.cli_common import (
    ExitCode,
    add_color_args,
    add_json_arg,
    add_verbose_and_debug_log_args,
    init_cli_runtime,
    run_cli_command,
)
from gerrit_workflow_tools.cli_style import (
    ANSI_BOLD,
    ANSI_CYAN,
    ANSI_DIM,
    ANSI_RED,
    ANSI_YELLOW,
    color_short_sha,
    color_stat,
    color_text,
    format_link,
)
from gerrit_workflow_tools.core.attention import Attention
from gerrit_workflow_tools.core.gerrit.rest import GerritRest
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.inbox import (
    OUTGOING,
    TO_REVIEW,
    InboxSection,
    load_inbox,
    pick_headline,
    project_scope,
    section_summary,
)
from gerrit_workflow_tools.core.review_chain import ReviewChain, format_age, host_from_web_base
from gerrit_workflow_tools.render.commit_row import fmt_code_review, fmt_comments, fmt_verified
from gerrit_workflow_tools.render.review_json import chain_json, iso_utc
from gerrit_workflow_tools.summary_highlight import SummaryHighlighter


_HEADINGS = {TO_REVIEW: "to review", OUTGOING: "outgoing"}
_EMPTY = {TO_REVIEW: "(nothing to review)", OUTGOING: "(no open chains of yours)"}


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
    add_json_arg(parser)
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
        verbose_help="List every chain member, not only those with attention.",
    )
    return parser


def _fmt_change_number(number: int) -> str:
    """Identity of a chain or member — same cyan as ``ger log`` SHAs."""
    return color_short_sha(f"c{number}")


def _fmt_status(verified: int | None, code_review: int | None, comments: int) -> str:
    """Verified / Code-Review / comments tokens — same palette as ``ger log``."""
    return f"{fmt_verified(verified)} {fmt_code_review(code_review)} {fmt_comments(comments)}"


def _fmt_unreviewed(seconds: int) -> str:
    """Waiting-on-you clock: dim label, yellow age (attention)."""
    return f"{color_text('unrevi', ANSI_DIM)} {color_text(format_age(seconds), ANSI_YELLOW)}"


def _fmt_activity(seconds: int) -> str:
    """Last-activity clock: dim label and age (context, not attention)."""
    return f"{color_text('act', ANSI_DIM)} {color_text(format_age(seconds), ANSI_DIM)}"


def _hash_suffix(body: str) -> str:
    return f"{color_text('# ', ANSI_DIM)}{body}"


def _attention_note(reasons: tuple[str, ...], comments: int) -> str:
    """Colored attention text, matching ``ger log`` trailing hints."""
    if Attention.CI_FAILED in reasons:
        return color_text("build failed", ANSI_RED)
    if Attention.UNRESOLVED_COMMENTS in reasons:
        noun = "comment" if comments == 1 else "comments"
        text = f"{comments} unresolved {noun}" if comments else "unresolved comments"
        return color_text(text, ANSI_YELLOW)
    if Attention.REVIEW_ISSUES in reasons:
        return color_text("negative vote", ANSI_YELLOW)
    return ""


def _render_chain_line(
    chain: ReviewChain,
    *,
    highlighter: SummaryHighlighter,
    show_url: bool,
    verbose: bool,
    outgoing: bool,
) -> None:
    """One chain. An outgoing chain is yours: no unreviewed clock and no owner."""
    subject = highlighter.highlight(chain.top.subject)
    waiting = "" if outgoing else f"{_fmt_unreviewed(chain.unreviewed_age_seconds)}  "
    owner = "" if outgoing else f"{color_text(chain.owner_name, ANSI_DIM)}   "
    line = (
        f"{_fmt_change_number(chain.top.number)}  {color_text(f'{chain.depth}c', ANSI_DIM)}  "
        f"{_fmt_status(chain.verified, chain.code_review, chain.comments_unresolved)}  "
        f"{waiting}{_fmt_activity(chain.wait_age_seconds)}  "
        f"{owner}{_hash_suffix(subject)}"
    )
    print(line)
    if show_url and chain.url:
        print(f"  {color_text(format_link(chain.url), ANSI_DIM)}")
    for member in chain.members:
        if member.number == chain.top.number and not verbose:
            continue
        if not verbose and not member.attention_reasons:
            continue
        note = _attention_note(member.attention_reasons, member.comments_unresolved)
        suffix_body = note if note else highlighter.highlight(member.subject)
        print(
            f"{color_text('   └ ', ANSI_DIM)}{_fmt_change_number(member.number)}  "
            f"{_fmt_status(member.verified, member.code_review, member.comments_unresolved)}  "
            f"{_hash_suffix(suffix_body)}"
        )


def _render_section(
    section: InboxSection,
    *,
    highlighter: SummaryHighlighter,
    show_url: bool,
    verbose: bool,
) -> None:
    chains = section.chains
    outgoing = section.name == OUTGOING
    print(color_text(f"{_HEADINGS[section.name]} ({len(chains)})", f"{ANSI_BOLD}{ANSI_CYAN}"))
    if not chains:
        print(color_text(_EMPTY[section.name], ANSI_DIM))
        return
    for chain in chains:
        _render_chain_line(chain, highlighter=highlighter, show_url=show_url, verbose=verbose, outgoing=outgoing)
    summary = section_summary(chains)
    sep = color_text(" · ", ANSI_DIM)
    oldest = (
        color_stat("oldest act ", format_age(summary["oldest_wait_seconds"]), ANSI_DIM)
        if outgoing
        else color_stat("oldest unrevi ", format_age(summary["oldest_unreviewed_seconds"]), ANSI_YELLOW)
    )
    parts = [
        color_text("summary:", f"{ANSI_BOLD}{ANSI_CYAN}"),
        " ",
        color_text(str(summary["chains"]), ANSI_CYAN),
        color_text(" chains", ANSI_DIM),
        sep,
        color_text(str(summary["changes"]), ANSI_CYAN),
        color_text(" changes", ANSI_DIM),
        sep,
        oldest,
    ]
    if summary["ci_failures"]:
        parts.extend([sep, color_stat("CI ", summary["ci_failures"], ANSI_RED)])
    if summary["comments"]:
        parts.extend([sep, color_stat("comments ", summary["comments"], ANSI_YELLOW)])
    print()
    print("".join(parts))


def _render_text(
    sections: list[InboxSection],
    *,
    projects: list[str],
    highlighter: SummaryHighlighter,
    show_url: bool,
    verbose: bool,
) -> None:
    scope = ", ".join(projects) if projects else "all projects"
    print(color_text(f"project: {scope}", ANSI_DIM))
    for section in sections:
        print()
        _render_section(section, highlighter=highlighter, show_url=show_url, verbose=verbose)


def main(argv: list[str] | None = None, *, gerrit: GerritRest | None = None) -> int:
    """CLI entry for ``ger inbox``."""
    return run_cli_command(lambda: _run(argv, gerrit=gerrit))


def _run(argv: list[str] | None, *, gerrit: GerritRest | None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    cwd, settings, highlighter = init_cli_runtime(args, repository=False)
    service = GerritService.from_cwd(cwd, settings=settings, rest=gerrit)
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
    )
    if limit is not None:
        sections = [InboxSection(s.name, s.query, s.chains[: max(0, limit)]) for s in sections]
    if args.json_:
        payload = {
            "host": host_from_web_base(service.web_base),
            "generated": iso_utc(now),
            "projects": projects,
            "sections": [
                {
                    "name": section.name,
                    "query": section.query,
                    "summary": section_summary(section.chains),
                    "chains": [chain_json(chain) for chain in section.chains],
                }
                for section in sections
            ],
        }
        print(json.dumps(payload, indent=2))
    else:
        _render_text(
            sections,
            projects=projects,
            highlighter=highlighter,
            show_url=bool(args.url),
            verbose=bool(args.verbose),
        )
    return int(ExitCode.ATTENTION) if pick_headline(sections) else int(ExitCode.OK)


if __name__ == "__main__":
    raise SystemExit(main())
