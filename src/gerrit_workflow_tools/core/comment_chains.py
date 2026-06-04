"""Gerrit comment chain threading and resolution logic.

Groups raw Gerrit file-map comment dicts into :class:`CommentChain` objects,
resolving ``in_reply_to`` links to find thread roots.
"""

from __future__ import annotations

from typing import Any

from gerrit_workflow_tools.core.gerrit_change_status import CommentChain, InlineComment


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


def _inline_comment_from_raw(path: str, comment: dict[str, Any]) -> InlineComment:
    from gerrit_workflow_tools.core.reviewer import format_gerrit_account_label

    raw_msg = comment.get("message")
    raw_id = comment.get("id")
    author_label: str | None = None
    raw_author = comment.get("author")
    if isinstance(raw_author, dict):
        author_label = format_gerrit_account_label(raw_author)
    return InlineComment(
        path=path,
        line=_comment_line(comment),
        message=raw_msg if isinstance(raw_msg, str) else "",
        comment_id=raw_id if isinstance(raw_id, str) else None,
        author=author_label,
    )


def build_comment_chains(file_map: dict[str, list[dict[str, Any]]]) -> list[CommentChain]:
    """Group Gerrit file-map comments into chains keyed by thread root."""

    by_id: dict[str, tuple[str, dict[str, Any]]] = {}
    for path, comments in file_map.items():
        for comment in comments:
            if not isinstance(comment, dict):
                continue
            raw_id = comment.get("id")
            if not isinstance(raw_id, str) or not raw_id.strip():
                continue
            by_id[raw_id] = (path, comment)

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

    chains: list[CommentChain] = []
    for root_id, items in grouped.items():
        items.sort(key=lambda pair: _comment_sort_key(pair[1]))
        raw_only = [c for _, c in items]
        root_path, root_comment = by_id[root_id]
        chains.append(
            CommentChain(
                root_id=root_id,
                path=root_path,
                line=_comment_line(root_comment),
                comments=tuple(_inline_comment_from_raw(p, c) for p, c in items),
                resolved=_chain_resolved_from_raw(raw_only),
            )
        )

    chains.sort(key=lambda ch: (ch.path, ch.line if ch.line is not None else -1))
    return chains


def collect_unresolved_comment_chains(file_map: dict[str, list[dict[str, Any]]]) -> list[CommentChain]:
    """Return comment chains whose last reply is still unresolved."""

    return [chain for chain in build_comment_chains(file_map) if not chain.resolved]


def collect_unresolved_comments(file_map: dict[str, list[dict[str, Any]]]) -> list[InlineComment]:
    """Flatten unresolved chains into individual comments (legacy flat list)."""

    rows: list[InlineComment] = []
    for chain in collect_unresolved_comment_chains(file_map):
        rows.extend(chain.comments)
    return rows
