"""Gerrit comment chain threading and resolution logic.

Groups raw Gerrit file-map comment dicts into :class:`CommentChain` objects,
resolving ``in_reply_to`` links to find thread roots.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from gerrit_workflow_tools.core.gerrit_change_status import CommentChain, ContextLine, InlineComment


if TYPE_CHECKING:
    from gerrit_workflow_tools.core.anchor_mapping import AnchorPlacement


CommentSelection = Literal["unresolved", "all", "resolved"]
COMMENT_SELECTIONS: tuple[CommentSelection, ...] = ("unresolved", "all", "resolved")


def count_unresolved_in_file_map(file_map: dict[str, list[dict[str, Any]]]) -> int:
    """Count unresolved comment chains (not raw comments) in a Gerrit file map."""

    return sum(1 for chain in build_comment_chains(file_map) if not chain.resolved)


def _comment_line(c: dict[str, Any]) -> int | None:
    ln = c.get("line")
    if isinstance(ln, int):
        return ln
    rng = c.get("range")
    if isinstance(rng, dict):
        start_line = rng.get("start_line")
        if isinstance(start_line, int):
            return start_line
    return None


def _comment_start_line(c: dict[str, Any]) -> int | None:
    rng = c.get("range")
    start_line = rng.get("start_line") if isinstance(rng, dict) else None
    return start_line if isinstance(start_line, int) else None


def _comment_sort_key(c: dict[str, Any]) -> str:
    updated = c.get("updated")
    if isinstance(updated, str):
        return updated
    return ""


def _chain_resolved_from_raw(comments: list[dict[str, Any]]) -> bool:
    """A chain is resolved when the last comment has ``unresolved`` not true."""
    if not comments:
        return True
    return comments[-1].get("unresolved") is not True


def _context_lines_from_raw(comment: dict[str, Any]) -> tuple[ContextLine, ...]:
    raw = comment.get("context_lines")
    if not isinstance(raw, list):
        return ()
    lines: list[ContextLine] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        number = item.get("line_number")
        text = item.get("context_line")
        if isinstance(number, int) and isinstance(text, str):
            lines.append(ContextLine(line_number=number, text=text))
    return tuple(lines)


def _inline_comment_from_raw(path: str, comment: dict[str, Any], *, draft: bool = False) -> InlineComment:
    from gerrit_workflow_tools.core.reviewer import format_gerrit_account_label

    raw_msg = comment.get("message")
    raw_id = comment.get("id")
    author_label: str | None = None
    raw_author = comment.get("author")
    if isinstance(raw_author, dict):
        author_label = format_gerrit_account_label(raw_author)
    patch_set = comment.get("patch_set")
    return InlineComment(
        path=path,
        line=_comment_line(comment),
        message=raw_msg if isinstance(raw_msg, str) else "",
        comment_id=raw_id if isinstance(raw_id, str) else None,
        author=author_label,
        context_lines=_context_lines_from_raw(comment),
        patch_set=patch_set if isinstance(patch_set, int) else None,
        unresolved=comment.get("unresolved") is True,
        draft=draft,
    )


def build_comment_chains(
    file_map: dict[str, list[dict[str, Any]]],
    drafts: dict[str, list[dict[str, Any]]] | None = None,
) -> list[CommentChain]:
    """Group Gerrit file-map comments into chains keyed by thread root.

    *drafts* (``GET …/drafts``) join the chain they reply to and sort after every
    published comment: publishing stamps them last, so they decide the state you will
    publish. ``resolved`` stays the published state. A chain of drafts only is your own
    unpublished comment.
    """

    by_id: dict[str, tuple[str, dict[str, Any]]] = {}
    draft_ids: set[str] = set()
    for source, from_drafts in ((file_map, False), (drafts or {}, True)):
        for path, comments in source.items():
            for comment in comments:
                if not isinstance(comment, dict):
                    continue
                raw_id = comment.get("id")
                if not isinstance(raw_id, str) or not raw_id.strip():
                    continue
                by_id[raw_id] = (path, comment)
                if from_drafts:
                    draft_ids.add(raw_id)

    def thread_root(comment_id: str) -> str:
        seen: set[str] = set()
        current = comment_id
        while current not in seen:
            seen.add(current)
            entry = by_id.get(current)
            if entry is None:
                return comment_id
            parent = entry[1].get("in_reply_to")
            if not isinstance(parent, str) or not parent.strip() or parent not in by_id:
                return current
            current = parent
        return current

    grouped: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for comment_id, (path, comment) in by_id.items():
        root = thread_root(comment_id)
        grouped.setdefault(root, []).append((path, comment))

    def is_draft(comment: dict[str, Any]) -> bool:
        return comment.get("id") in draft_ids

    chains: list[CommentChain] = []
    for root_id, items in grouped.items():
        items.sort(key=lambda pair: (is_draft(pair[1]), _comment_sort_key(pair[1])))
        published = [c for _, c in items if not is_draft(c)]
        root_path, root_comment = by_id[root_id]
        chains.append(
            CommentChain(
                root_id=root_id,
                path=root_path,
                line=_comment_line(root_comment),
                comments=tuple(_inline_comment_from_raw(p, c, draft=is_draft(c)) for p, c in items),
                resolved=_chain_resolved_from_raw(published or [c for _, c in items]),
                start_line=_comment_start_line(root_comment),
                on_parent=root_comment.get("side") == "PARENT",
            )
        )

    chains.sort(key=lambda ch: (ch.path, ch.line if ch.line is not None else -1))
    return chains


def select_chains(chains: list[CommentChain], comments: CommentSelection) -> list[CommentChain]:
    """*chains* filtered by *comments* on their published state; ``all`` puts unresolved first."""
    if comments == "all":
        return [chain for chain in chains if not chain.resolved] + [chain for chain in chains if chain.resolved]
    if comments == "resolved":
        return [chain for chain in chains if chain.resolved]
    return [chain for chain in chains if not chain.resolved]


def local_comment_chain(comment_id: str, comment: InlineComment, anchor: AnchorPlacement) -> CommentChain:
    """A note or local draft as a chain of its own, where *anchor* puts it: always a root, never a reply."""
    return CommentChain(
        root_id=comment_id,
        path=anchor.shown.path,
        line=anchor.shown.line,
        comments=(comment,),
        resolved=not comment.unresolved,
        start_line=anchor.shown.start_line,
        anchor=anchor,
    )


def merge_chains(gerrit: list[CommentChain], local: list[CommentChain]) -> list[CommentChain]:
    """Gerrit and local chains in one list: by path and line, Gerrit's first at a location."""
    merged = [*gerrit, *local]
    return sorted(merged, key=lambda ch: (ch.resolved, ch.path, ch.line if ch.line is not None else -1, ch.local))


def collect_comment_chains(
    file_map: dict[str, list[dict[str, Any]]],
    *,
    comments: CommentSelection = "unresolved",
    drafts: dict[str, list[dict[str, Any]]] | None = None,
) -> list[CommentChain]:
    """Return comment chains filtered by *comments*, on their published state.

    ``all`` lists unresolved chains first (path/line order within each group), then
    resolved ones. That keeps the attention-bearing threads at the top.
    """

    return select_chains(build_comment_chains(file_map, drafts), comments)


def collect_unresolved_comment_chains(file_map: dict[str, list[dict[str, Any]]]) -> list[CommentChain]:
    """Return comment chains whose last reply is still unresolved."""

    return collect_comment_chains(file_map, comments="unresolved")
