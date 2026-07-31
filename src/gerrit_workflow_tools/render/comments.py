"""Human and Markdown rendering for Gerrit unresolved comment chains."""

from __future__ import annotations

from gerrit_workflow_tools.cli_style import ANSI_BOLD, ANSI_CYAN, ANSI_DIM, ANSI_YELLOW, color_text
from gerrit_workflow_tools.core.gerrit_change_status import CommentChain, gerrit_inline_comment_url


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


def format_comment_chain_human(
    chain: CommentChain,
    gerrit_url: str | None,
    *,
    tail_n: int,
    full: bool,
) -> list[str]:
    """Return human-readable lines for one unresolved comment chain (no trailing blank)."""
    loc = chain_location(chain)
    lines: list[str] = [f"  {color_text(loc, ANSI_BOLD + ANSI_CYAN)}"]
    chain_url = gerrit_inline_comment_url(gerrit_url, chain.root_id) or gerrit_url
    if chain_url:
        lines.append(f"  {color_text('url:', ANSI_DIM)} {color_text(chain_url, ANSI_YELLOW)}")

    comments = chain.comments
    for i, row_item in enumerate(comments):
        is_reply = i > 0
        is_last = i == len(comments) - 1
        if is_reply:
            gutter = color_text("└ " if is_last else "│ ", ANSI_DIM)
            author_prefix = f"  {gutter}"
            body_prefix = f"  {color_text('│ ' if not is_last else '  ', ANSI_DIM)}"
        else:
            author_prefix = "  "
            body_prefix = "    "

        if row_item.author:
            lines.append(f"{author_prefix}{color_text(row_item.author, ANSI_DIM)}")
        body, _trunc = apply_comment_tail(row_item.message, tail_n, full=full)
        for ln in body.splitlines():
            lines.append(f"{body_prefix}{ln}")
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
    """Section header plus chain blocks for human output."""
    out = [color_text("Unresolved comments:", ANSI_YELLOW)]
    if not pushed:
        out.append("  (not on Gerrit — no comments)")
        return out
    if not chains:
        out.append("  (no unresolved comments)")
        return out
    for i, chain in enumerate(chains):
        out.extend(format_comment_chain_human(chain, gerrit_url, tail_n=tail_n, full=full))
        if i < len(chains) - 1:
            out.append("")
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
