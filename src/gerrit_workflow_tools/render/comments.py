"""Human and Markdown rendering for Gerrit inline comment chains."""

from __future__ import annotations

from gerrit_workflow_tools.cli_style import (
    ANSI_BLUE,
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
from gerrit_workflow_tools.core.gerrit_change_status import (
    CommentChain,
    ContextLine,
    InlineComment,
    gerrit_inline_comment_url,
)
from gerrit_workflow_tools.render.wrap import fit_header, wrap_lines


# Minimum inner width so short threads still look like a box.
_MIN_BOX_INNER = 40
_BOX_INDENT = "    "
_DEFAULT_WRAP_WIDTH = 120
# ``│ `` before the row, so a row of `width` needs this many extra columns in the span.
_ROW_TO_INNER = 1
# Top border is ``─ {heading} ─`` plus one trailing dash: 4 characters around the heading.
_HEADING_CHROME = 4


def chain_location(chain: CommentChain) -> str:
    """``path:line`` when a line is known, ``path:start-end`` for a range, otherwise just the path."""
    if chain.start_line is not None and chain.start_line != chain.line:
        return f"{chain.path}:{chain.start_line}-{chain.line}"
    if chain.line is not None:
        return f"{chain.path}:{chain.line}"
    return chain.path


# How a local comment is headed, human and Markdown.
LOCAL_LABELS = {"note": "note (private)", "local-draft": "local draft (not on Gerrit)"}
_LOCAL_LABELS_MARKDOWN = {"note": "Note (private)", "local-draft": "Local draft (not on Gerrit)"}


def anchor_suffix(chain: CommentChain) -> str:
    """`` (outdated: lines changed since)``, or `` (moved from L)`` when a clean mapping moved it."""
    anchor = chain.anchor
    if anchor is None or not anchor.moved:
        return ""
    if anchor.outdated:
        return " (outdated: lines changed since)"
    if anchor.shown == anchor.anchored:
        return ""
    was = anchor.anchored.describe()
    if anchor.anchored.path == anchor.shown.path:
        was = was.removeprefix(f"{anchor.anchored.path}:")
    return f" (moved from {was})"


def chain_heading(chain: CommentChain) -> str:
    """Location, plus where a local comment's anchor moved from, and ``(resolved)`` for a closed chain."""
    loc = chain_location(chain) + anchor_suffix(chain)
    if chain.resolved:
        return f"{loc} (resolved)"
    return loc


def _chain_url(chain: CommentChain, gerrit_url: str | None) -> str | None:
    """The chain's Gerrit URL; a local chain has none."""
    if chain.local:
        return None
    return gerrit_inline_comment_url(gerrit_url, chain.root_id) or gerrit_url


def draft_label(chain: CommentChain, comment: InlineComment, *, label: str = "draft (you)") -> str:
    """*label*, plus how your newest draft changes the chain's state once published."""
    if comment is not chain.draft or chain.resolved == chain.resolved_after_publish:
        return label
    return f"{label} -> {'resolves' if chain.resolved_after_publish else 'reopens'}"


def draft_transition(chain: CommentChain, unresolved: bool) -> str:
    """`` -> resolves`` / `` -> reopens`` when a draft flagged *unresolved* changes *chain*'s state."""
    if chain.resolved == (not unresolved):
        return ""
    return " -> reopens" if unresolved else " -> resolves"


def first_line(text: str) -> str:
    """The first line of *text*, stripped; empty for blank text."""
    return text.strip().splitlines()[0] if text.strip() else ""


def drafts_in(chains: list[CommentChain]) -> list[tuple[CommentChain, InlineComment]]:
    """Every draft in *chains*, with the chain it sits in."""
    return [(chain, c) for chain in chains for c in chain.comments if c.draft]


def format_pending_drafts(heading: str, chains: list[CommentChain]) -> list[str]:
    """*heading*, then one line per draft about to be published: where, the state change, its text."""
    lines = [heading]
    for chain, draft in drafts_in(chains):
        state = draft_transition(chain, draft.unresolved) if draft is chain.draft else ""
        lines.append(f'  {chain_location(chain)}{state}: "{first_line(draft.message)}"')
    return lines


def _box_border(text: str, *, resolved: bool) -> str:
    return color_text(text, ANSI_DIM_GRAY if resolved else ANSI_YELLOW)


def _pad_inner(text: str, inner_width: int) -> str:
    pad = max(0, inner_width - visible_len(text))
    return f"{text}{' ' * pad}"


def _format_context_line(line: ContextLine) -> str:
    return f"{line.line_number:>4}  {line.text}"


def _chain_context_lines(chain: CommentChain) -> tuple[ContextLine, ...]:
    if chain.anchor is not None and chain.anchor.outdated and chain.anchor.text:
        # The lines it was written on are gone from this version: show them as they were.
        return chain.anchor.context_lines
    for row_item in chain.comments:
        if row_item.context_lines:
            return row_item.context_lines
    return ()


def _prefixed_wrap(text: str, width: int, *, prefix: str) -> list[str]:
    """Wrap *text* so ``prefix + line`` stays within *width* when there is room."""
    room = width - len(prefix)
    if room < 1:
        return wrap_lines(prefix + text, width)
    return [f"{prefix}{part}" for part in wrap_lines(text, room)]


def _box_content_rows(
    chain: CommentChain,
    gerrit_url: str | None,
    *,
    width: int,
) -> list[str]:
    """Inner lines of a comment box (no borders).

    Comment text, authors, and context lines are wrapped to *width*. The chain
    URL is left whole so it stays copyable, and may widen the box by itself.
    """
    rows: list[str] = []
    context = _chain_context_lines(chain)
    if context:
        for line in context:
            for part in wrap_lines(_format_context_line(line), width):
                rows.append(color_text(part, ANSI_DIM))
        rows.append("")
    for row_item in chain.comments:
        if row_item.local is not None:
            rows.append(color_text(LOCAL_LABELS[row_item.local], ANSI_BOLD + ANSI_CYAN))
            for ln in row_item.message.splitlines() or [""]:
                rows.extend(color_text(part, ANSI_CYAN) for part in _prefixed_wrap(ln, width, prefix="  "))
            continue
        if row_item.draft:
            rows.append(color_text(draft_label(chain, row_item), ANSI_BOLD + ANSI_BLUE))
            for ln in row_item.message.splitlines() or [""]:
                rows.extend(color_text(part, ANSI_BLUE) for part in _prefixed_wrap(ln, width, prefix="  "))
            continue
        if row_item.author:
            for part in wrap_lines(row_item.author, width):
                rows.append(color_text(part, ANSI_DIM))
        for ln in row_item.message.splitlines() or [""]:
            rows.extend(_prefixed_wrap(ln, width, prefix="  "))
    chain_url = _chain_url(chain, gerrit_url)
    if chain_url:
        rows.append(f"{color_text('url:', ANSI_DIM)} {color_text(format_link(chain_url), ANSI_YELLOW)}")
    return rows


def format_comment_chain_human(
    chain: CommentChain,
    gerrit_url: str | None,
    *,
    width: int = _DEFAULT_WRAP_WIDTH,
) -> list[str]:
    """Return human-readable lines for one comment chain in a rounded box.

    Unresolved chains use a yellow border; resolved chains use grey and a
    ``(resolved)`` header so the state survives ``--color=never``.
    """
    heading = fit_header(chain_heading(chain), max(1, width - _HEADING_CHROME))
    loc_styled = color_text(heading, ANSI_BOLD + ANSI_CYAN)
    rows = _box_content_rows(chain, gerrit_url, width=width)
    resolved = chain.resolved

    # Top mid is ``─ {heading} ─…`` (3 fixed chars around heading). Content lines
    # use ``│ `` + row, so row width needs +1 vs the inner span between corners.
    # A long chain URL is the only row allowed to push the box past *width*.
    inner_width = max(
        min(_MIN_BOX_INNER, width),
        _HEADING_CHROME + visible_len(heading),
        *(_ROW_TO_INNER + visible_len(r) for r in rows),
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
    heading = f"### `{loc}`{anchor_suffix(chain)}"
    if chain.resolved:
        heading = f"{heading} (resolved)"
    lines: list[str] = [heading]
    chain_url = _chain_url(chain, gerrit_url)
    if chain_url:
        lines.append(chain_url)
        lines.append("")

    context = _chain_context_lines(chain)
    if context:
        lines.append("```")
        for line in context:
            lines.append(_format_context_line(line))
        lines.append("```")
        lines.append("")

    for row_item in chain.comments:
        if row_item.local is not None:
            lines.append(f"**{_LOCAL_LABELS_MARKDOWN[row_item.local]}**")
        elif row_item.draft:
            lines.append(f"**{draft_label(chain, row_item, label='Draft reply (unpublished)')}**")
        else:
            lines.append(f"**{row_item.author or 'anonymous'}**")
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
    width: int = _DEFAULT_WRAP_WIDTH,
) -> list[str]:
    """Boxed comment chains for human output (empty list when there are none).

    A commit Gerrit does not have can still carry local comments; *pushed* no longer hides them.
    """
    del pushed
    if not chains:
        return []
    out: list[str] = []
    for chain in chains:
        out.extend(format_comment_chain_human(chain, gerrit_url, width=width))
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
    if not pushed and not chains:
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
