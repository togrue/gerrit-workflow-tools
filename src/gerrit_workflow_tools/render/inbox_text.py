"""``ger inbox``'s human text: one row per review chain, by inbox section, then a summary.

Rows are cells padded to the widest of their column in the section, so they line up whatever the
ages and names. On a terminal the subject is cut to fit the width; piped, nothing is cut.
"""

from __future__ import annotations

import sys

from gerrit_workflow_tools.core.attention import Attention
from gerrit_workflow_tools.core.changeish import change_label
from gerrit_workflow_tools.core.inbox import (
    OUTGOING,
    TO_REVIEW,
    InboxSection,
    needs_author,
    not_ready_why,
    section_summary,
)
from gerrit_workflow_tools.core.review_chain import ChainMember, ReviewChain, format_age
from gerrit_workflow_tools.core.words import counted
from gerrit_workflow_tools.render.commit_row import fmt_code_review, fmt_comments, fmt_verified
from gerrit_workflow_tools.render.status_fmt import AUTHOR, REVIEWER, SECTION_HEADINGS, member_hints
from gerrit_workflow_tools.render.style import (
    ANSI_BOLD,
    ANSI_CYAN,
    ANSI_DIM,
    ANSI_DIM_GRAY,
    ANSI_RED,
    ANSI_YELLOW,
    color_short_sha,
    color_stat,
    color_text,
    format_link,
    is_hyperlink_enabled,
    terminal_columns,
    visible_len,
)
from gerrit_workflow_tools.render.wrap import say
from gerrit_workflow_tools.summary_highlight import SummaryHighlighter


_EMPTY = {TO_REVIEW: "(nothing to review)", OUTGOING: "(no open chains of yours)"}
_OWNER_WIDTH = 18
_MIN_SUBJECT = 12
"""Narrower than this, a cut subject says too little: the row wraps instead."""


def _fmt_change_number(number: int, url: str | None) -> str:
    """Identity of a chain or member, as every changeish argument takes it back; ``ger log``'s SHA cyan.

    With terminal hyperlinks on, the label opens the change: no URL line is needed below the row.
    """
    label = color_short_sha(change_label(number))
    return format_link(url, label=label) if url and is_hyperlink_enabled() else label


def _fmt_votes(item: ReviewChain | ChainMember) -> str:
    """Verified / Code-Review / comments tokens — same palette as ``ger log``."""
    comments = fmt_comments(item.comments_unresolved, item.comments_to_answer)
    return f"{fmt_verified(item.verified)} {fmt_code_review(item.code_review)} {comments}"


def _fmt_chain_votes(chain: ReviewChain) -> str:
    """The chain's votes; over several changes, the worst cast of each, said so (not the top's own)."""
    if chain.depth == 1:
        return _fmt_votes(chain)
    verified, code_review = chain.verified_tally.cast, chain.code_review_tally.cast
    comments = fmt_comments(chain.comments_unresolved, chain.comments_to_answer)
    return f"{color_text('worst', ANSI_DIM)} {fmt_verified(verified)} {fmt_code_review(code_review)} {comments}"


def _fmt_missing(chain: ReviewChain) -> str:
    """``v missing on 2, cr on 1``: the labels some members of the chain have no vote on. A label no
    member has a vote on says so in its own token (``v?``)."""
    parts = [
        (name, tally.missing)
        for name, tally in (("v", chain.verified_tally), ("cr", chain.code_review_tally))
        if 0 < tally.missing < chain.depth
    ]
    words = [f"{name} missing on {n}" if i == 0 else f"{name} on {n}" for i, (name, n) in enumerate(parts)]
    return color_text(", ".join(words), ANSI_YELLOW) if words else ""


def _fmt_size(depth: int) -> str:
    return color_text(counted(depth, "change"), ANSI_DIM)


def _fmt_age(label: str, seconds: int, color: str) -> str:
    return f"{color_text(label, ANSI_DIM)} {color_text(format_age(seconds), color)}"


def _fmt_owner(name: str) -> str:
    shown = name if len(name) <= _OWNER_WIDTH else f"{name[: _OWNER_WIDTH - 1]}…"
    return color_text(shown, ANSI_DIM)


_MEMBER_HINT_COLOR = {Attention.CI_FAILED: ANSI_RED}


def _attention_note(member: ChainMember, *, outgoing: bool) -> str:
    """The most urgent of :func:`member_hints`, coloured as ``ger log``'s trailing hints."""
    hints = member_hints(member, side=REVIEWER if outgoing else AUTHOR)
    return color_text(hints[0][1], _MEMBER_HINT_COLOR.get(hints[0][0], ANSI_YELLOW)) if hints else ""


def _marks(work_in_progress: bool, private: bool) -> str:
    """``ger log``'s WIP and private hints, as a trailing ``# …``."""
    words = [color_text("WIP", ANSI_YELLOW)] * work_in_progress + [color_text("private", ANSI_DIM_GRAY)] * private
    return f"  {color_text('# ', ANSI_DIM)}{', '.join(words)}" if words else ""


def _drafts_mark(chain: ReviewChain, drafted: frozenset[int]) -> str:
    """``# drafts of yours on c2142``: what ``ger reply --publish`` would send there."""
    numbers = [change_label(m.number) for m in chain.members if m.number in drafted]
    return (
        f"  {color_text('# ', ANSI_DIM)}{color_text('drafts of yours on ' + ', '.join(numbers), ANSI_YELLOW)}"
        if numbers
        else ""
    )


_VOTES = 2
"""The votes cell, right-aligned: a single change's tokens sit under the ``worst`` ones of a chain."""


def _pad(cells: list[str], widths: list[int]) -> str:
    def fit(i: int, cell: str) -> str:
        gap = " " * (widths[i] - visible_len(cell))
        return gap + cell if i == _VOTES else cell + gap

    return "  ".join(
        fit(i, cell) for i, cell in enumerate(cells) if widths[i]
    )  # a column empty in every row takes no room


_INDENT = "  "


def _subject(text: str, *, used: int, columns: int | None, highlighter: SummaryHighlighter) -> str:
    """``# subject``, cut with ``…`` to what *columns* leave after *used* visible characters (one
    spare, so a terminal never wraps). Too little room: on a line of its own below, cut to fit there."""
    room = None if columns is None else columns - used - 3
    if room is not None and room < _MIN_SUBJECT:
        return "\n" + _INDENT + _subject(text, used=len(_INDENT), columns=columns, highlighter=highlighter)
    if room is not None and len(text) > room:
        text = f"{text[: room - 1]}…"
    return f"{color_text('# ', ANSI_DIM)}{highlighter.highlight(text)}"


def _chain_cells(chain: ReviewChain, *, outgoing: bool) -> list[str]:
    """The aligned part of a chain's row. An outgoing chain is yours: no waiting clock and no owner.
    Nor has a chain you reviewed that waits on its author: it waits on nobody's review."""
    cells = [
        _fmt_change_number(chain.top.number, chain.url),
        _fmt_size(chain.depth),
        _fmt_chain_votes(chain),
        _fmt_missing(chain),
    ]
    if outgoing or not chain.your_turn:
        cells.append(_fmt_age("active", chain.wait_age_seconds, ANSI_DIM))
        return cells if outgoing else [*cells, _fmt_owner(chain.owner_name)]
    waiting = _fmt_age("waiting", chain.unreviewed_age_seconds, ANSI_YELLOW)
    # Last activity only when it says more than the waiting clock: touched since, or not since long before.
    if format_age(chain.wait_age_seconds) != format_age(chain.unreviewed_age_seconds):
        waiting += f"{color_text(',', ANSI_DIM)} {_fmt_age('active', chain.wait_age_seconds, ANSI_DIM)}"
    return [*cells, waiting, _fmt_owner(chain.owner_name)]


def _member_lines(
    chain: ReviewChain, *, verbose: bool, outgoing: bool, columns: int | None, highlighter: SummaryHighlighter
) -> list[str]:
    """The members below a chain's row: those with attention, to review those not ready, every one with *verbose*."""
    lines: list[str] = []
    for member in chain.members:
        not_ready = not outgoing and not member.matched
        # A single change's own row says it all; a chain's row says the worst, so the member says which.
        if chain.depth == 1 or not (verbose or member.attention_reasons or not_ready):
            continue
        head = f"{color_text('  └ ', ANSI_DIM)}{_fmt_change_number(member.number, member.url)}  {_fmt_votes(member)}  "
        if not_ready:  # in the chain, not in your queue: dim, and why
            lines.append(f"{head}{color_text(f'# not ready: {not_ready_why(member)}', ANSI_DIM)}")
            continue
        note = _attention_note(member, outgoing=outgoing)
        if note:
            lines.append(f"{head}{color_text('# ', ANSI_DIM)}{note}")
            continue
        marks = _marks(member.work_in_progress, member.private)
        used = visible_len(head) + visible_len(marks)
        lines.append(head + _subject(member.subject, used=used, columns=columns, highlighter=highlighter) + marks)
    return lines


def _heading(section: InboxSection, shown: list[ReviewChain]) -> str:
    """``to review (5; 3 your turn; 1 not ready, --unready shows it)``, ``outgoing (22; 5 need you)``."""
    notes: list[str] = []
    if section.name == TO_REVIEW and section.waiting_on_author:
        notes.append(f"{len(shown) or 'none'} your turn")
    if section.not_ready:
        notes.append(f"{section.not_ready} not ready, --unready shows {'it' if section.not_ready == 1 else 'them'}")
    if section.name == OUTGOING and section.chains:
        notes.append(f"{len(shown) or 'none'} need{'s' if len(shown) == 1 else ''} you")
    count = f"{len(section.chains)}{'; ' + '; '.join(notes) if notes else ''}"
    return color_text(f"{SECTION_HEADINGS[section.name]} ({count})", f"{ANSI_BOLD}{ANSI_CYAN}")


def _summary(section: InboxSection, *, outgoing: bool) -> str:
    summary = section_summary(section)
    sep = color_text(" · ", ANSI_DIM)
    oldest = (
        color_stat("most idle ", format_age(summary["oldest_wait_seconds"]), ANSI_DIM)
        if outgoing
        else color_stat("longest wait ", format_age(summary["oldest_unreviewed_seconds"]), ANSI_YELLOW)
    )
    parts = [
        color_text("summary:", f"{ANSI_BOLD}{ANSI_CYAN}"),
        " ",
        color_text(str(summary["chains"]), ANSI_CYAN),
        color_text(" chain" if summary["chains"] == 1 else " chains", ANSI_DIM),
        sep,
        color_text(str(summary["changes"]), ANSI_CYAN),
        color_text(" change" if summary["changes"] == 1 else " changes", ANSI_DIM),
        sep,
        oldest,
    ]
    if summary["ci_failures"]:
        parts.extend([sep, color_stat("build failed ", summary["ci_failures"], ANSI_RED)])
    if summary["comments"]:
        parts.extend([sep, color_stat("comments ", summary["comments"], ANSI_YELLOW)])
    return "".join(parts)


def _render_section(
    section: InboxSection,
    *,
    highlighter: SummaryHighlighter,
    show_url: bool,
    verbose: bool,
    columns: int | None,
) -> None:
    chains = section.chains
    outgoing = section.name == OUTGOING
    # What wants you is listed: outgoing, chains that need their author; to review, chains whose
    # turn is yours. The others fold into one line; -v (and --json, --ai) list them.
    wanted = [chain for chain in chains if needs_author(chain)] if outgoing else section.waiting_on_you
    shown = chains if verbose else wanted
    print(_heading(section, wanted))
    if not chains:
        print(color_text(_EMPTY[section.name], ANSI_DIM))
        return
    rows = [_chain_cells(chain, outgoing=outgoing) for chain in shown]
    widths = [max(visible_len(row[i]) for row in rows) for i in range(len(rows[0]))] if rows else []
    for chain, cells in zip(shown, rows, strict=True):
        head = _pad(cells, widths) + "  "
        marks = (_marks(chain.work_in_progress, chain.private) if outgoing else "") + _drafts_mark(
            chain, section.drafted
        )
        used = visible_len(head) + visible_len(marks)
        print(head + _subject(chain.top.subject, used=used, columns=columns, highlighter=highlighter) + marks)
        if show_url and chain.url and not is_hyperlink_enabled():  # else the row's cN opens it
            print(f"{_INDENT}{color_text(chain.url, ANSI_DIM)}")
        for line in _member_lines(chain, verbose=verbose, outgoing=outgoing, columns=columns, highlighter=highlighter):
            print(line)
    if folded := len(chains) - len(shown):
        lead = "+ " if shown else ""
        those = "chain waits" if folded == 1 else "chains wait"
        on = "reviewers or CI" if outgoing else "the author"
        print(color_text(f"{lead}{folded} {those} on {on} (-v lists them)", ANSI_DIM))
    if not wanted and not outgoing:  # nothing waits on your review: no summary, no next step
        return
    print()
    print(_summary(section, outgoing=outgoing))
    if not outgoing:
        say(_review_hint(wanted[0]), file=sys.stdout, dim=True)


def _review_hint(chain: ReviewChain) -> str:
    """The command that starts on the chain waiting longest: at its bottom, where review begins.

    Named by the row's change, with ``ger review --bottom``: the same command works for every row.
    """
    if chain.depth == 1:
        return f"next: ger review {change_label(chain.top.number)}"
    return f"next: ger review {change_label(chain.top.number)} --bottom, then --next"


def render_inbox_text(
    sections: list[InboxSection],
    *,
    projects: list[str],
    highlighter: SummaryHighlighter,
    show_url: bool,
    verbose: bool,
) -> None:
    """Print the inbox *sections* for a human: rows aligned, subjects cut to a terminal's width."""
    scope = ", ".join(projects) if projects else "all projects"
    print(color_text(f"project: {scope}", ANSI_DIM))
    columns = terminal_columns()
    for section in sections:
        print()
        _render_section(section, highlighter=highlighter, show_url=show_url, verbose=verbose, columns=columns)
