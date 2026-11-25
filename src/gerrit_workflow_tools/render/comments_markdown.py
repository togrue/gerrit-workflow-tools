"""Markdown rendering for Gerrit inline comment chains: ``ger show --ai`` and the agent runbook."""

from __future__ import annotations

from gerrit_workflow_tools.core.comment_anchor import SPECIAL_PATHS
from gerrit_workflow_tools.core.comment_chains import CommentSelection
from gerrit_workflow_tools.core.gerrit_change_status import CommentChain
from gerrit_workflow_tools.core.suggested_edits import SuggestedEdit
from gerrit_workflow_tools.render.comments import (
    anchor_suffix,
    chain_context_lines,
    chain_location,
    chain_state,
    chain_url,
    comment_meta,
    draft_label,
    empty_comments_message,
    format_context_line,
)
from gerrit_workflow_tools.render.suggested_fixes import fixes_markdown


_LOCAL_LABELS_MARKDOWN = {"note": "Note (private)", "local-draft": "Local draft (not on Gerrit)"}


def format_comment_chain_markdown(
    chain: CommentChain,
    gerrit_url: str | None,
    *,
    level: int = 3,
) -> list[str]:
    """Return Markdown lines for one comment chain (full bodies, no ANSI)."""
    loc = chain_location(chain)
    # A file is code-spanned; Gerrit's special paths read as words.
    heading = f"{loc if chain.path in SPECIAL_PATHS else f'`{loc}`'}{anchor_suffix(chain)}{chain_state(chain)}"
    lines: list[str] = [f"{'#' * level} {heading}"]
    if chain.root_id:
        lines.append(f"Reply with `ger reply {chain.root_id}`")
    url = chain_url(chain, gerrit_url)
    if url:
        lines.append(url)
        lines.append("")

    context = chain_context_lines(chain)
    if context:
        lines.append("```")
        for line in context:
            lines.append(format_context_line(line))
        lines.append("```")
        lines.append("")

    for row_item in chain.comments:
        meta = comment_meta(row_item)
        meta = f" · {meta}" if meta else ""
        if row_item.local is not None:
            lines.append(f"**{_LOCAL_LABELS_MARKDOWN[row_item.local]}**")
        elif row_item.draft:
            lines.append(f"**{draft_label(chain, row_item, label='Draft reply (unpublished)')}**{meta}")
        else:
            lines.append(f"**{row_item.author or 'anonymous'}**{meta}")
        body = row_item.message.rstrip("\n")
        if body:
            for ln in body.splitlines() or [""]:
                lines.append(f"> {ln}" if ln else ">")
        else:
            lines.append(">")
        lines.extend(_fix_suggestions_markdown(row_item.suggested_edits))
        lines.append("")
    # Drop the trailing blank between chains; caller adds separation.
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _fix_suggestions_markdown(edits: tuple[SuggestedEdit, ...]) -> list[str]:
    """The Gerrit fix suggestions in *edits*, quoted under the comment, each replacement fenced."""
    return [line for fix in fixes_markdown(edits) for line in (">", *(f"> {text}".rstrip() for text in fix))]


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
            out.extend(format_comment_chain_markdown(chain, gerrit_url, level=4))
            if i < len(unresolved) - 1:
                out.append("")
    if resolved:
        if out:
            out.append("")
        out.append("### Resolved comments")
        out.append("")
        for i, chain in enumerate(resolved):
            out.extend(format_comment_chain_markdown(chain, gerrit_url, level=4))
            if i < len(resolved) - 1:
                out.append("")
    return out
