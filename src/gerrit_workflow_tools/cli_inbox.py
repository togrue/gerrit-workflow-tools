"""CLI for ``ger inbox``: open review chains waiting on you."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from gerrit_workflow_tools.cli_common import (
    HELP_JSON,
    ExitCode,
    add_color_args,
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
    color_text,
    format_link,
)
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.rest import GerritRest
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.review_chain import ChainMember, ReviewChain, format_age, host_from_web_base
from gerrit_workflow_tools.render.commit_row import fmt_code_review, fmt_comments, fmt_verified
from gerrit_workflow_tools.summary_highlight import SummaryHighlighter

DEFAULT_TO_REVIEW_QUERY = "is:open -is:wip -is:private -owner:self reviewer:self"
DEFAULT_TO_REVIEW_ALL_QUERY = "is:open -owner:self reviewer:self"


def build_to_review_query(
    settings: Settings,
    *,
    projects: list[str],
    include_unready: bool,
    cwd: Path | str | None = None,
) -> str:
    """Section query for *to review*, with verified gate and project filters folded in."""
    from gerrit_workflow_tools.core.inbox_strategy import build_to_review_query_via_registry

    names = list(projects) if projects else list(settings.inbox_projects)

    def _builtin() -> str:
        custom = settings.inbox_to_review_query
        if custom:
            base = custom
        elif include_unready:
            base = DEFAULT_TO_REVIEW_ALL_QUERY
        else:
            base = DEFAULT_TO_REVIEW_QUERY
            if settings.inbox_require_verified:
                base = f"{base} label:{settings.inbox_verified_label}+1"
        if not names:
            return base
        if len(names) == 1:
            return f"{base} project:{names[0]}"
        inner = " OR ".join(f"project:{name}" for name in names)
        return f"{base} ({inner})"

    # Wholesale git-config override still wins over scripting.
    if settings.inbox_to_review_query:
        return _builtin()

    project = settings.gerrit_project or (names[0] if len(names) == 1 else "")
    default_query = _builtin()
    scripted = build_to_review_query_via_registry(
        cwd if cwd is not None else Path.cwd(),
        project=project,
        settings=settings,
        projects=names,
        include_unready=include_unready,
        default_query=default_query,
        web_base=settings.gerrit_web_url,
    )
    return scripted if scripted is not None else default_query


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
        type=int,
        default=None,
        metavar="N",
        help="At most N chains.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_",
        help=HELP_JSON,
    )
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


def _member_payload(member: ChainMember) -> dict[str, object]:
    return {
        "number": member.number,
        "change_id": member.change_id,
        "subject": member.subject,
        "verified": member.verified,
        "code_review": member.code_review,
        "comments_unresolved": member.comments_unresolved,
        "attention_reasons": list(member.attention_reasons),
        "url": member.url,
    }


def _chain_json(chain: ReviewChain) -> dict[str, object]:
    last_activity = chain.last_activity.isoformat().replace("+00:00", "Z") if chain.last_activity else None
    return {
        "key": chain.key,
        "top": {
            "number": chain.top.number,
            "change_id": chain.top.change_id,
            "subject": chain.top.subject,
            "url": chain.top.url,
        },
        "project": chain.project,
        "branch": chain.branch,
        "owner": {"name": chain.owner_name, "email": chain.owner_email},
        "depth": chain.depth,
        "wait_age_seconds": chain.wait_age_seconds,
        "unreviewed_age_seconds": chain.unreviewed_age_seconds,
        "last_activity": last_activity,
        "verified": chain.verified,
        "code_review": chain.code_review,
        "comments_unresolved": chain.comments_unresolved,
        "attention_reasons": list(chain.attention_reasons),
        "partial_chain": chain.partial_chain,
        "members": [_member_payload(member) for member in chain.members],
    }


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
    if "ci-failed" in reasons:
        return color_text("build failed", ANSI_RED)
    if "unresolved-comments" in reasons:
        noun = "comment" if comments == 1 else "comments"
        text = f"{comments} unresolved {noun}" if comments else "unresolved comments"
        return color_text(text, ANSI_YELLOW)
    if "review-issues" in reasons:
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
    changes = sum(chain.depth for chain in chains)
    oldest = max((chain.unreviewed_age_seconds for chain in chains), default=0)
    ci = sum(1 for chain in chains if "ci-failed" in chain.attention_reasons)
    comments = sum(1 for chain in chains if "unresolved-comments" in chain.attention_reasons)
    sep = color_text(" · ", ANSI_DIM)
    parts = [
        color_text("summary:", f"{ANSI_BOLD}{ANSI_CYAN}"),
        " ",
        color_text(str(len(chains)), ANSI_CYAN),
        color_text(" chains", ANSI_DIM),
        sep,
        color_text(str(changes), ANSI_CYAN),
        color_text(" changes", ANSI_DIM),
        sep,
        color_text("oldest unrevi ", ANSI_DIM),
        color_text(format_age(oldest), ANSI_YELLOW),
    ]
    if ci:
        parts.extend([sep, color_text("CI ", ANSI_DIM), color_text(str(ci), ANSI_RED)])
    if comments:
        parts.extend([sep, color_text("comments ", ANSI_DIM), color_text(str(comments), ANSI_YELLOW)])
    print()
    print("".join(parts))


def main(argv: list[str] | None = None, *, gerrit: GerritRest | None = None) -> int:
    """CLI entry for ``ger inbox``."""
    return run_cli_command(lambda: _run(argv, gerrit=gerrit))


def _run(argv: list[str] | None, *, gerrit: GerritRest | None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    cwd, settings, highlighter = init_cli_runtime(
        debug_log=args.debug_log, color=args.color, hyperlinks=args.hyperlinks
    )
    service = GerritService.from_cwd(cwd, settings=settings, rest=gerrit)
    query = build_to_review_query(
        settings,
        projects=list(args.project),
        include_unready=bool(args.include_unready),
        cwd=cwd,
    )
    limit = args.limit if args.limit is not None else settings.inbox_limit
    now = datetime.now(timezone.utc)
    chains = service.fetch_review_chains(query, now=now)
    if limit is not None:
        chains = chains[: max(0, limit)]
    if args.json_:
        payload = {
            "host": host_from_web_base(service.web_base),
            "generated": now.isoformat().replace("+00:00", "Z"),
            "sections": [
                {
                    "name": "to-review",
                    "query": query,
                    "chains": [_chain_json(chain) for chain in chains],
                }
            ],
            "summary": {
                "chains": len(chains),
                "changes": sum(chain.depth for chain in chains),
                "oldest_unreviewed_seconds": max((chain.unreviewed_age_seconds for chain in chains), default=0),
                "oldest_wait_seconds": max((chain.wait_age_seconds for chain in chains), default=0),
                "ci_failures": sum(1 for chain in chains if "ci-failed" in chain.attention_reasons),
                "comments": sum(1 for chain in chains if "unresolved-comments" in chain.attention_reasons),
            },
        }
        print(json.dumps(payload, indent=2))
        return int(ExitCode.ATTENTION) if chains else int(ExitCode.OK)
    _render_text(
        chains,
        highlighter=highlighter,
        show_url=bool(args.url),
        verbose=bool(args.verbose),
    )
    return int(ExitCode.ATTENTION) if chains else int(ExitCode.OK)


if __name__ == "__main__":
    raise SystemExit(main())
