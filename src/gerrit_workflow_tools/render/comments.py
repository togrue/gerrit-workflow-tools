"""Human and Markdown rendering for Gerrit inline comment chains."""

from __future__ import annotations

from gerrit_workflow_tools.cli_style import (
    ANSI_BOLD,
    ANSI_CYAN,
    ANSI_DIM,
    ANSI_DIM_GRAY,
    ANSI_YELLOW,
    color_text,
    format_link,
    visible_len,
)
from gerrit_workflow_tools.core.comment_chains import CommentSelection
from gerrit_workflow_tools.core.gerrit_change_status import CommentChain, gerrit_inline_comment_url


# Minimum inner width so short threads still look like a box.
_MIN_BOX_INNER = 40
_BOX_INDENT = "    "


def chain_location(chain: CommentChain) -> str:
    """``path:line`` when a line is known, otherwise just the path."""
    if chain.line is not None:
        return f"{chain.path}:{chain.line}"
    return chain.path


def chain_heading(chain: CommentChain) -> str:
    """Location plus a ``(resolved)`` marker when the chain is closed."""
    loc = chain_location(chain)
    if chain.resolved:
        return f"{loc} (resolved)"
    return loc


def _box_border(text: str, *, resolved: bool) -> str:
    return color_text(text, ANSI_DIM_GRAY if resolved else ANSI_YELLOW)


def _pad_inner(text: str, inner_width: int) -> str:
    pad = max(0, inner_width - visible_len(text))
    return f"{text}{' ' * pad}"


def _box_content_rows(
    chain: CommentChain,
    gerrit_url: str | None,
) -> list[str]:
    """Inner lines of a comment box (no borders)."""
    rows: list[str] = []
    for row_item in chain.comments:
        if row_item.author:
            rows.append(color_text(row_item.author, ANSI_DIM))
        for ln in row_item.message.splitlines() or [""]:
            rows.append(f"  {ln}")
    chain_url = gerrit_inline_comment_url(gerrit_url, chain.root_id) or gerrit_url
    if chain_url:
        rows.append(f"{color_text('url:', ANSI_DIM)} {color_text(format_link(chain_url), ANSI_YELLOW)}")
    return rows


def format_comment_chain_human(
    chain: CommentChain,
    gerrit_url: str | None,
) -> list[str]:
    """Return human-readable lines for one comment chain in a rounded box.

    Unresolved chains use a yellow border; resolved chains use grey and a
    ``(resolved)`` header so the state survives ``--color=never``.
    """
    heading = chain_heading(chain)
    loc_styled = color_text(heading, ANSI_BOLD + ANSI_CYAN)
    rows = _box_content_rows(chain, gerrit_url)
    resolved = chain.resolved

    # Top mid is ``─ {heading} ─…`` (3 fixed chars around heading). Content lines
    # use ``│ `` + row, so row width needs +1 vs the inner span between corners.
    inner_width = max(
        _MIN_BOX_INNER,
        3 + visible_len(heading) + 1,
        *(1 + visible_len(r) for r in rows),
    )
    dashes = max(1, inner_width - 3 - visible_len(heading))
    top_mid = f"{_box_border('─', resolved=resolved)} {loc_styled} {_box_border('─' * dashes, resolved=resolved)}"
    top_line = f"{_BOX_INDENT}{_box_border('╭', resolved=resolved)}{top_mid}{_box_border('╮', resolved=resolved)}"
    bottom_line = (
        f"{_BOX_INDENT}{_box_border('╰', resolved=resolved)}"
        f"{_box_border('─' * inner_width, resolved=resolved)}"
        f"{_box_border('╯', resolved=resolved)}"
    )

    lines: list[str] = [top_line]
    content_width = max(1, inner_width - 1)
    for row in rows:
        lines.append(
            f"{_BOX_INDENT}{_box_border('│', resolved=resolved)} "
            f"{_pad_inner(row, content_width)}"
            f"{_box_border('│', resolved=resolved)}"
        )
    lines.append(bottom_line)
    return lines


def format_comment_chain_markdown(
    chain: CommentChain,
    gerrit_url: str | None,
) -> list[str]:
    """Return Markdown lines for one comment chain (full bodies, no ANSI)."""
    loc = chain_location(chain)
    heading = f"### `{loc}`"
    if chain.resolved:
        heading = f"{heading} (resolved)"
    lines: list[str] = [heading]
    chain_url = gerrit_inline_comment_url(gerrit_url, chain.root_id) or gerrit_url
    if chain_url:
        lines.append(chain_url)
        lines.append("")

    for row_item in chain.comments:
        author = row_item.author or "anonymous"
        lines.append(f"**{author}**")
        body = row_item.message.rstrip("\n")
        if body:
            for ln in body.splitlines() or [""]:
                lines.append(f"> {ln}" if ln else ">")
        else:
            lines.append(">")
        lines.append("")
    # Drop the trailing blank between chains; caller adds separation.
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def format_unresolved_section_human(
    chains: list[CommentChain],
    gerrit_url: str | None,
    *,
    pushed: bool,
) -> list[str]:
    """Boxed comment chains for human output (empty list when there are none)."""
    if not pushed or not chains:
        return []
    out: list[str] = []
    for chain in chains:
        out.extend(format_comment_chain_human(chain, gerrit_url))
    return out


def empty_comments_message(comments: CommentSelection) -> str:
    """One-line empty state matching the ``--comments`` selection."""
    if comments == "resolved":
        return "(no resolved comments)"
    if comments == "all":
        return "(no comments)"
    return "(no unresolved comments)"


def format_unresolved_section_markdown(
    chains: list[CommentChain],
    gerrit_url: str | None,
    *,
    pushed: bool,
    comments: CommentSelection = "unresolved",
) -> list[str]:
    """Markdown comment blocks (full bodies), split by resolved state when needed."""
    if not pushed:
        return ["### Unresolved comments", "", "(not on Gerrit — no comments)"]
    if not chains:
        heading = "### Resolved comments" if comments == "resolved" else "### Unresolved comments"
        if comments == "all":
            heading = "### Comments"
        return [heading, "", empty_comments_message(comments)]

    unresolved = [chain for chain in chains if not chain.resolved]
    resolved = [chain for chain in chains if chain.resolved]
    out: list[str] = []
    if unresolved:
        out.append("### Unresolved comments")
        out.append("")
        for i, chain in enumerate(unresolved):
            out.extend(format_comment_chain_markdown(chain, gerrit_url))
            if i < len(unresolved) - 1:
                out.append("")
    if resolved:
        if out:
            out.append("")
        out.append("### Resolved comments")
        out.append("")
        for i, chain in enumerate(resolved):
            out.extend(format_comment_chain_markdown(chain, gerrit_url))
            if i < len(resolved) - 1:
                out.append("")
    return out
