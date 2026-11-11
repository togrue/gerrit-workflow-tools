"""CLI for ``ger inbox``: open review chains waiting on you."""

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
from gerrit_workflow_tools.core.inbox import build_to_review_query, section_summary
from gerrit_workflow_tools.core.review_chain import ReviewChain, format_age, host_from_web_base
from gerrit_workflow_tools.render.commit_row import fmt_code_review, fmt_comments, fmt_verified
from gerrit_workflow_tools.render.review_json import chain_json, iso_utc
from gerrit_workflow_tools.summary_highlight import SummaryHighlighter


def _limit_value(raw: str) -> int:
    if not raw.isdigit() or int(raw) < 1:
        raise argparse.ArgumentTypeError(f"must be a positive number: {raw!r}")
    return int(raw)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ger inbox",
        description="Open Gerrit review chains waiting on you (to-review overview).",
    )
    parser.add_argument(
        "--to-review",
        action="store_true",
        help="Only the to-review section (default; the only section implemented so far).",
    )
    parser.add_argument(
        "--project",
        action="append",
        default=[],
        metavar="P",
        help="Restrict to a Gerrit project. Repeatable.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        dest="include_unready",
        help="Include chains filtered out as not ready (WIP, private, CI red).",
    )
    parser.add_argument(
        "--limit",
        type=_limit_value,
        default=None,
        metavar="N",
        help="At most N chains.",
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
) -> None:
    subject = highlighter.highlight(chain.top.subject)
    line = (
        f"{_fmt_change_number(chain.top.number)}  {color_text(f'{chain.depth}c', ANSI_DIM)}  "
        f"{_fmt_status(chain.verified, chain.code_review, chain.comments_unresolved)}  "
        f"{_fmt_unreviewed(chain.unreviewed_age_seconds)}  "
        f"{_fmt_activity(chain.wait_age_seconds)}  "
        f"{color_text(chain.owner_name, ANSI_DIM)}   {_hash_suffix(subject)}"
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


def _render_text(
    chains: list[ReviewChain],
    *,
    highlighter: SummaryHighlighter,
    show_url: bool,
    verbose: bool,
) -> None:
    heading = f"to review ({len(chains)})"
    print(color_text(heading, f"{ANSI_BOLD}{ANSI_CYAN}"))
    if not chains:
        print(color_text("(nothing to review)", ANSI_DIM))
        return
    for chain in chains:
        _render_chain_line(chain, highlighter=highlighter, show_url=show_url, verbose=verbose)
    summary = section_summary(chains)
    sep = color_text(" · ", ANSI_DIM)
    parts = [
        color_text("summary:", f"{ANSI_BOLD}{ANSI_CYAN}"),
        " ",
        color_text(str(summary["chains"]), ANSI_CYAN),
        color_text(" chains", ANSI_DIM),
        sep,
        color_text(str(summary["changes"]), ANSI_CYAN),
        color_text(" changes", ANSI_DIM),
        sep,
        color_stat("oldest unrevi ", format_age(summary["oldest_unreviewed_seconds"]), ANSI_YELLOW),
    ]
    if summary["ci_failures"]:
        parts.extend([sep, color_stat("CI ", summary["ci_failures"], ANSI_RED)])
    if summary["comments"]:
        parts.extend([sep, color_stat("comments ", summary["comments"], ANSI_YELLOW)])
    print()
    print("".join(parts))


def main(argv: list[str] | None = None, *, gerrit: GerritRest | None = None) -> int:
    """CLI entry for ``ger inbox``."""
    return run_cli_command(lambda: _run(argv, gerrit=gerrit))


def _run(argv: list[str] | None, *, gerrit: GerritRest | None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    cwd, settings, highlighter = init_cli_runtime(args, repository=False)
    service = GerritService.from_cwd(cwd, settings=settings, rest=gerrit)
    query = build_to_review_query(
        settings,
        projects=list(args.project),
        include_unready=bool(args.include_unready),
    )
    limit = args.limit if args.limit is not None else settings.inbox_limit
    now = datetime.now(timezone.utc)
    chains = service.fetch_review_chains(query, now=now)
    if limit is not None:
        chains = chains[: max(0, limit)]
    if args.json_:
        payload = {
            "host": host_from_web_base(service.web_base),
            "generated": iso_utc(now),
            "sections": [
                {
                    "name": "to-review",
                    "query": query,
                    "chains": [chain_json(chain) for chain in chains],
                }
            ],
            "summary": section_summary(chains),
        }
        print(json.dumps(payload, indent=2))
    else:
        _render_text(chains, highlighter=highlighter, show_url=bool(args.url), verbose=bool(args.verbose))
    return int(ExitCode.ATTENTION) if chains else int(ExitCode.OK)


if __name__ == "__main__":
    raise SystemExit(main())
