"""Human and Markdown rendering for Gerrit unresolved comment chains."""

from __future__ import annotations

from gerrit_workflow_tools.cli_style import (
    ANSI_BOLD,
    ANSI_CYAN,
    ANSI_DIM,
    ANSI_YELLOW,
    color_text,
    visible_len,
)
from gerrit_workflow_tools.core.gerrit_change_status import CommentChain, gerrit_inline_comment_url

# Minimum inner width so short threads still look like a box.
_MIN_BOX_INNER = 40
_BOX_INDENT = "    "


def apply_comment_tail(text: str, tail_lines: int, *, full: bool) -> tuple[str, bool]:
    """Return ``(body, truncated)`` applying last-N-line truncation unless *full*."""
    if full:
        return text, False
    lines = text.splitlines()
    if len(lines) <= tail_lines:
        return text, False
    omitted = len(lines) - tail_lines
    body = "\n".join(lines[-tail_lines:])
    return f"[... {omitted} lines omitted above]\n{body}", True


def chain_location(chain: CommentChain) -> str:
    """``path:line`` when a line is known, otherwise just the path."""
    if chain.line is not None:
        return f"{chain.path}:{chain.line}"
    return chain.path


def _box_border(text: str) -> str:
    return color_text(text, ANSI_YELLOW)


def _pad_inner(text: str, inner_width: int) -> str:
    pad = max(0, inner_width - visible_len(text))
    return f"{text}{' ' * pad}"


def _box_content_rows(
    chain: CommentChain,
    gerrit_url: str | None,
    *,
    tail_n: int,
    full: bool,
) -> list[str]:
    """Inner lines of a comment box (no borders)."""
    rows: list[str] = []
    for row_item in chain.comments:
        if row_item.author:
            rows.append(color_text(row_item.author, ANSI_DIM))
        body, _trunc = apply_comment_tail(row_item.message, tail_n, full=full)
        for ln in body.splitlines() or [""]:
            rows.append(f"  {ln}")
    chain_url = gerrit_inline_comment_url(gerrit_url, chain.root_id) or gerrit_url
    if chain_url:
        rows.append(f"{color_text('url:', ANSI_DIM)} {color_text(chain_url, ANSI_YELLOW)}")
    return rows


def format_comment_chain_human(
    chain: CommentChain,
    gerrit_url: str | None,
    *,
    tail_n: int,
    full: bool,
) -> list[str]:
    """Return human-readable lines for one unresolved comment chain in a yellow rounded box."""
    loc = chain_location(chain)
    loc_styled = color_text(loc, ANSI_BOLD + ANSI_CYAN)
    rows = _box_content_rows(chain, gerrit_url, tail_n=tail_n, full=full)

    # Top mid is ``─ {loc} ─…`` (3 fixed chars around loc). Content lines use
    # ``│ `` + row, so row width needs +1 vs the inner span between corners.
    inner_width = max(
        _MIN_BOX_INNER,
        3 + visible_len(loc) + 1,
        *(1 + visible_len(r) for r in rows),
    )
    dashes = max(1, inner_width - 3 - visible_len(loc))
    top_mid = f"{_box_border('─')} {loc_styled} {_box_border('─' * dashes)}"
    top_line = f"{_BOX_INDENT}{_box_border('╭')}{top_mid}{_box_border('╮')}"
    bottom_line = f"{_BOX_INDENT}{_box_border('╰')}{_box_border('─' * inner_width)}{_box_border('╯')}"

    lines: list[str] = [top_line]
    content_width = max(1, inner_width - 1)
    for row in rows:
        lines.append(
            f"{_BOX_INDENT}{_box_border('│')} {_pad_inner(row, content_width)}{_box_border('│')}"
        )
    lines.append(bottom_line)
    return lines


def format_comment_chain_markdown(
    chain: CommentChain,
    gerrit_url: str | None,
) -> list[str]:
    """Return Markdown lines for one unresolved comment chain (full bodies, no ANSI)."""
    loc = chain_location(chain)
    lines: list[str] = [f"### `{loc}`"]
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
    tail_n: int,
    full: bool,
) -> list[str]:
    """Boxed unresolved chains for human output (empty list when there are none)."""
    if not pushed or not chains:
        return []
    out: list[str] = []
    for chain in chains:
        out.extend(format_comment_chain_human(chain, gerrit_url, tail_n=tail_n, full=full))
    return out


def format_unresolved_section_markdown(
    chains: list[CommentChain],
    gerrit_url: str | None,
    *,
    pushed: bool,
) -> list[str]:
    """Markdown unresolved-comments block (full bodies)."""
    out = ["### Unresolved comments"]
    if not pushed:
        out.append("")
        out.append("(not on Gerrit — no comments)")
        return out
    if not chains:
        out.append("")
        out.append("(no unresolved comments)")
        return out
    out.append("")
    for i, chain in enumerate(chains):
        out.extend(format_comment_chain_markdown(chain, gerrit_url))
        if i < len(chains) - 1:
            out.append("")
    return out
