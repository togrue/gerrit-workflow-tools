"""Human rendering for Gerrit inline comment chains, and the words both views share (``comments_markdown``)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone

from gerrit_workflow_tools.core.changeish import change_label
from gerrit_workflow_tools.core.comment_chains import CommentSelection
from gerrit_workflow_tools.core.gerrit.rest import printable_path
from gerrit_workflow_tools.core.gerrit_change_status import (
    CommentChain,
    ContextLine,
    InlineComment,
    gerrit_inline_comment_url,
)
from gerrit_workflow_tools.core.review_chain import format_age
from gerrit_workflow_tools.core.words import counted
from gerrit_workflow_tools.render.style import (
    ANSI_BLUE,
    ANSI_BOLD,
    ANSI_CYAN,
    ANSI_DIM,
    ANSI_DIM_GRAY,
    ANSI_GREEN,
    ANSI_YELLOW,
    color_text,
    format_link,
    visible_len,
)
from gerrit_workflow_tools.render.suggested_fixes import fix_rows
from gerrit_workflow_tools.render.wrap import fit_header, wrap_lines


# Minimum inner width so short threads still look like a box.
_MIN_BOX_INNER = 40
_BOX_INDENT = "    "
_DEFAULT_WRAP_WIDTH = 120
# ``│ `` before the row and a space before the closing ``│``: a row of `width` needs this many more in the span.
_ROW_TO_INNER = 2
# Top border is ``─ {heading} ─`` plus one trailing dash: 4 characters around the heading.
_HEADING_CHROME = 4
# A box is at most this wide, indent and borders included, however wide the terminal.
BOX_MAX_COLUMNS = 100
# What a box adds around its rows: the indent, ``│ `` and `` │``.
BOX_CHROME = len("    ") + 4


def box_width(wrap_width: int, terminal: int | None) -> int:
    """The width a box wraps its rows to: the terminal's, at most :data:`BOX_MAX_COLUMNS`, within *wrap_width*."""
    columns = min(terminal or BOX_MAX_COLUMNS, BOX_MAX_COLUMNS)
    return max(_MIN_BOX_INNER - _ROW_TO_INNER, min(wrap_width, columns - BOX_CHROME))


def comment_meta(comment: InlineComment, *, now: datetime | None = None) -> str:
    """``PS1 · 2h ago``: which patchset it is on and how old it is; empty when neither is known."""
    parts = [f"PS{comment.patch_set}"] if comment.patch_set else []
    if comment.updated is not None:
        age = ((now or datetime.now(timezone.utc)) - comment.updated).total_seconds()
        parts.append(f"{format_age(int(age))} ago")
    return " · ".join(parts)


def _with_meta(label: str, comment: InlineComment) -> str:
    meta = comment_meta(comment)
    return f"{label} · {meta}" if meta else label


# Gerrit's names for what is not a file, as a person reads them.
SPECIAL_PATHS = {
    "/PATCHSET_LEVEL": "Patchset-level comment",
    "/COMMIT_MSG": "Commit message",
    "/MERGE_LIST": "Merge list",
}


def chain_location(chain: CommentChain) -> str:
    """``path:line`` when a line is known, ``path:start-end`` for a range, otherwise just the path.

    Gerrit's special paths are named in words: ``Patchset-level comment``, ``Commit message:12``.
    """
    if chain.path == "/PATCHSET_LEVEL":
        return SPECIAL_PATHS[chain.path]
    path = SPECIAL_PATHS.get(chain.path) or printable_path(chain.path)
    if chain.start_line is not None and chain.start_line != chain.line:
        return f"{path}:{chain.start_line}-{chain.line}"
    if chain.line is not None:
        return f"{path}:{chain.line}"
    return path


# How a local comment is headed, human and Markdown.
LOCAL_LABELS = {"note": "note (private)", "local-draft": "local draft (not on Gerrit)"}


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
        was = was.removeprefix(f"{printable_path(anchor.anchored.path)}:")
    return f" (moved from {was})"


def chain_heading(chain: CommentChain) -> str:
    """Location, plus where a local comment's anchor moved from, and ``(resolved)`` for a closed chain."""
    loc = chain_location(chain) + anchor_suffix(chain)
    if chain.resolved:
        return f"{loc} (resolved)"
    return loc


def chain_url(chain: CommentChain, gerrit_url: str | None) -> str | None:
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


def format_context_line(line: ContextLine) -> str:
    return f"{line.line_number:>4}  {line.text}"


def chain_context_lines(chain: CommentChain) -> tuple[ContextLine, ...]:
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

    Comment text, authors, and context lines are wrapped to *width*. The chain URL is not
    among them (:func:`_url_row`).
    """
    rows: list[str] = []
    context = chain_context_lines(chain)
    if context:
        for line in context:
            for part in wrap_lines(format_context_line(line), width):
                rows.append(color_text(part, ANSI_DIM))
        rows.append("")
    for row_item in chain.comments:
        if row_item.local is not None:
            rows.append(color_text(LOCAL_LABELS[row_item.local], ANSI_BOLD + ANSI_CYAN))
            for ln in row_item.message.splitlines() or [""]:
                rows.extend(color_text(part, ANSI_CYAN) for part in _prefixed_wrap(ln, width, prefix="  "))
            continue
        if row_item.draft:
            rows.append(color_text(_with_meta(draft_label(chain, row_item), row_item), ANSI_BOLD + ANSI_BLUE))
            for ln in row_item.message.splitlines() or [""]:
                rows.extend(color_text(part, ANSI_BLUE) for part in _prefixed_wrap(ln, width, prefix="  "))
        else:
            for part in wrap_lines(_with_meta(row_item.author or "anonymous", row_item), width):
                rows.append(color_text(part, ANSI_DIM))
            for ln in row_item.message.splitlines() or [""]:
                rows.extend(_prefixed_wrap(ln, width, prefix="  "))
        for fix in fix_rows(row_item.suggested_edits):
            for indent, text, wraps in fix:
                prefix = f"  {indent}"
                parts = _prefixed_wrap(text, width, prefix=prefix) if wraps else [f"{prefix}{text}"]
                rows.extend(color_text(part, ANSI_GREEN) for part in parts)
    return rows


def _url_row(chain: CommentChain, gerrit_url: str | None, *, label: bool = True) -> str | None:
    url = chain_url(chain, gerrit_url)
    if not url:
        return None
    link = color_text(format_link(url), ANSI_YELLOW)
    return f"{color_text('url:', ANSI_DIM)} {link}" if label else link


def format_comment_chain_human(
    chain: CommentChain,
    gerrit_url: str | None,
    *,
    width: int = _DEFAULT_WRAP_WIDTH,
    min_inner: int = 0,
) -> list[str]:
    """Return human-readable lines for one comment chain in a rounded box, at least *min_inner* wide inside.

    The top border names where it is and, on the right, the id ``ger reply`` takes. Unresolved
    chains use a yellow border; resolved chains use grey and a ``(resolved)`` header so the state
    survives ``--color=never``.
    """
    chain_id = chain.root_id or ""
    id_chrome = len(chain_id) + 3 if chain_id else 0  # `` {id} ─``
    heading = fit_header(chain_heading(chain), max(1, width - _HEADING_CHROME - id_chrome))
    rows = _box_content_rows(chain, gerrit_url, width=width)
    resolved = chain.resolved
    # The URL stays whole, to be copied: in the box when it fits, else on its own line below it.
    url = _url_row(chain, gerrit_url)
    url_inside = url is not None and visible_len(url) <= width
    if url is not None and url_inside:
        rows.append(url)

    def border(text: str) -> str:
        return _box_border(text, resolved=resolved)

    inner_width = max(
        min_inner,
        min(_MIN_BOX_INNER, width),
        _HEADING_CHROME + visible_len(heading) + id_chrome,
        *(_ROW_TO_INNER + visible_len(r) for r in rows),
    )
    dashes = max(1, inner_width - 3 - visible_len(heading) - id_chrome)
    top_mid = f"{border('─')} {color_text(heading, ANSI_BOLD + ANSI_CYAN)} {border('─' * dashes)}"
    if chain_id:
        top_mid += f" {color_text(chain_id, ANSI_DIM)} {border('─')}"
    lines = [f"{_BOX_INDENT}{border('╭')}{top_mid}{border('╮')}"]
    content_width = max(1, inner_width - _ROW_TO_INNER)
    lines += [f"{_BOX_INDENT}{border('│')} {_pad_inner(row, content_width)} {border('│')}" for row in rows]
    lines.append(f"{_BOX_INDENT}{border('╰')}{border('─' * inner_width)}{border('╯')}")
    if not url_inside and (bare := _url_row(chain, gerrit_url, label=False)):
        lines.append(f"{_BOX_INDENT}{bare}")
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
    # One width for every box of a commit, so they line up: the widest one's.
    boxes = [format_comment_chain_human(chain, gerrit_url, width=width) for chain in chains]
    inner = max(visible_len(box[0]) for box in boxes) - len(_BOX_INDENT) - 2
    return [
        line for chain in chains for line in format_comment_chain_human(chain, gerrit_url, width=width, min_inner=inner)
    ]


def empty_comments_message(comments: CommentSelection) -> str:
    """One-line empty state matching the ``--comments`` selection."""
    if comments == "resolved":
        return "(no resolved comments)"
    if comments == "all":
        return "(no comments)"
    return "(no unresolved comments)"


def drafts_elsewhere(counts: Mapping[int, int | None]) -> str:
    """``1 unpublished draft on c2076: ger reply --publish c2076``, for drafts of yours that a step
    leaves behind; a count of ``None`` is not known (a ``has:draft`` answer alone)."""
    labels = [change_label(number) for number in counts]
    known = [count for count in counts.values() if count is not None]
    total = sum(known) if len(known) == len(counts) else None
    what = "unpublished drafts" if total is None else counted(total, "unpublished draft")
    return f"{what} on {', '.join(labels)}: ger reply --publish {' '.join(labels)}"
