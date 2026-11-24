"""``ger inbox``'s human text: one row per review chain, by inbox section, then a summary."""

from __future__ import annotations

from gerrit_workflow_tools.core.attention import Attention
from gerrit_workflow_tools.core.inbox import OUTGOING, TO_REVIEW, InboxSection, section_summary
from gerrit_workflow_tools.core.review_chain import ReviewChain, format_age
from gerrit_workflow_tools.render.commit_row import fmt_code_review, fmt_comments, fmt_verified
from gerrit_workflow_tools.render.status_fmt import SECTION_HEADINGS, member_hints
from gerrit_workflow_tools.render.style import (
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
from gerrit_workflow_tools.summary_highlight import SummaryHighlighter


_EMPTY = {TO_REVIEW: "(nothing to review)", OUTGOING: "(no open chains of yours)"}


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


_MEMBER_HINT_COLOR = {Attention.CI_FAILED: ANSI_RED}


def _attention_note(reasons: tuple[str, ...], comments: int) -> str:
    """The most urgent of :func:`member_hints`, coloured as ``ger log``'s trailing hints."""
    hints = member_hints(reasons, comments)
    return color_text(hints[0][1], _MEMBER_HINT_COLOR.get(hints[0][0], ANSI_YELLOW)) if hints else ""


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
    print(color_text(f"{SECTION_HEADINGS[section.name]} ({len(chains)})", f"{ANSI_BOLD}{ANSI_CYAN}"))
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


def render_inbox_text(
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
