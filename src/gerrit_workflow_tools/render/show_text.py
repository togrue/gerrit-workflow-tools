"""``ger show``'s human text for one commit: the headline, attention, author, links, message, CI, comments.

The command reads what to show (``core/gerrit_show.py``) and the commit's author and message
(``core.stack.read_commit_meta``); this only lays it out, as lines.
"""

from __future__ import annotations

from datetime import datetime, timezone

from gerrit_workflow_tools.core.attention import Attention
from gerrit_workflow_tools.core.comment_chains import CommentSelection
from gerrit_workflow_tools.core.gerrit_change_status import LogCommit
from gerrit_workflow_tools.core.gerrit_show import ShownCommit
from gerrit_workflow_tools.core.review_chain import format_age
from gerrit_workflow_tools.core.stack import CommitMeta
from gerrit_workflow_tools.render.ci_content import format_ci_section_human
from gerrit_workflow_tools.render.comments import box_width, empty_comments_message, format_unresolved_section_human
from gerrit_workflow_tools.render.commit_row import attention_summary, continuation_lines, show_headline, votes_text
from gerrit_workflow_tools.render.status_fmt import chain_blocked_text
from gerrit_workflow_tools.render.style import ANSI_DIM, ANSI_YELLOW, color_text, format_link, terminal_width
from gerrit_workflow_tools.render.wrap import wrap_lines
from gerrit_workflow_tools.summary_highlight import SummaryHighlighter


def commit_separator() -> str:
    """The line between two commits of a multi-commit view."""
    return color_text("═" * 64, ANSI_YELLOW)


def _body_lines(body: str, *, width: int, sha: str | None, summary_highlighter: SummaryHighlighter) -> list[str]:
    """A commit message, each line wrapped, the subject highlighted; a blank line first."""
    body_lines = body.splitlines()
    if not body_lines:
        return []
    out = [""]
    for index, line in enumerate(body_lines):
        parts = wrap_lines(line, width)
        if index == 0:
            parts = [summary_highlighter.highlight(part, sha=sha) for part in parts]
        out += [f"    {part}" for part in parts]
    return out


def _when(iso_date: str) -> str:
    """``, 3h ago (2026-11-24 04:39)`` for an ISO author date; empty when there is none."""
    try:
        when = datetime.fromisoformat(iso_date)
    except ValueError:
        return f", {iso_date}" if iso_date else ""
    age = format_age(int((datetime.now(timezone.utc) - when).total_seconds()))
    return f", {age} ago ({when:%Y-%m-%d %H:%M})"


def commit_lines(
    item: ShownCommit, meta: CommitMeta | None, *, summary_highlighter: SummaryHighlighter, wrap_width: int
) -> list[str]:
    """One commit in full; *meta* is its author and message: the local commit's, else Gerrit's (``None``: neither)."""
    commit, chains, ci_contents = item.commit, item.chains, item.ci_contents
    out = [show_headline(commit)]
    # A blocked commit names its blocker on a line of its own, so the reasons need not say chain-blocked too.
    said = frozenset({Attention.CHAIN_BLOCKED}) if item.blocked_by is not None else frozenset()
    if commit.attention_reasons and (summary := attention_summary(commit, leave_out=said)):
        out.append(color_text(summary, ANSI_DIM))
    if item.blocked_by is not None:
        out.append(color_text(chain_blocked_text(item.blocked_by), ANSI_DIM))
    if meta is not None:
        out.append(f"Author: {meta.author_line}{_when(meta.date)}")
    if commit.gerrit_url:
        out.append(f"{color_text('url:', ANSI_DIM)} {color_text(format_link(commit.gerrit_url), ANSI_YELLOW)}")
    if votes := votes_text(commit):
        out.append(f"{color_text('votes:', ANSI_DIM)} {votes}")
    out += continuation_lines(commit, verbose_level=1)
    if meta is not None and meta.body:
        out += _body_lines(meta.body, width=wrap_width, sha=commit.sha, summary_highlighter=summary_highlighter)
    elif commit.summary:
        out.append(f"\n    {commit.summary}")
    if ci_contents:
        out += [f"\n{color_text('CI findings:', ANSI_DIM)}", *format_ci_section_human(ci_contents)]
    width = box_width(wrap_width, terminal_width())
    if comment_lines := format_unresolved_section_human(chains, commit.gerrit_url, pushed=commit.pushed, width=width):
        out += ["", *comment_lines]
    return out


def brief_commit_lines(commit: LogCommit, *, comments: CommentSelection) -> list[str]:
    """One commit of a multi-commit view that has no comments to expand."""
    return [show_headline(commit), f"    {commit.summary}", color_text(empty_comments_message(comments), ANSI_DIM)]
