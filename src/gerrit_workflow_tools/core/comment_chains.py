"""Gerrit comment chain threading and resolution logic, and whose turn a chain is.

Groups raw Gerrit file-map comment dicts into :class:`CommentChain` objects,
resolving ``in_reply_to`` links to find thread roots. :func:`chain_turn` is the one verdict
on whose turn an unresolved chain is; attention counts it (:func:`chain_turns`).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal, TypeVar
from urllib.parse import quote, unquote

from gerrit_workflow_tools.core.gerrit_change_status import ChainTurn, CommentChain, ContextLine, InlineComment
from gerrit_workflow_tools.core.suggested_edits import suggested_edits_from_raw


if TYPE_CHECKING:
    from gerrit_workflow_tools.core.anchor_mapping import AnchorPlacement


CommentSelection = Literal["unresolved", "all", "resolved"]
COMMENT_SELECTIONS: tuple[CommentSelection, ...] = ("unresolved", "all", "resolved")


def chain_turn(chain: CommentChain, me: int | None) -> ChainTurn | None:
    """Whose turn *chain* is, *me* being your account id; ``None`` when *me* is not known.

    Published comments decide: when your own is the last word, the chain waits on the other side,
    drafts of yours or not. Otherwise it waits on you, to publish your answer when it is a draft.
    """
    if me is None:
        return None
    published = [c for c in chain.comments if not c.draft]
    if published and published[-1].author_account_id == me:
        return ChainTurn.THEIRS
    return ChainTurn.DRAFT if chain.draft is not None else ChainTurn.YOURS


@dataclass(frozen=True)
class ChainTurns:
    """How a change's unresolved comment chains split by whose turn they are (:func:`chain_turn`)."""

    unresolved: int = 0
    """Every unresolved chain, whoever's turn: the fact Gerrit counts."""
    theirs: int = 0
    """Your published comment is the last word: they wait on the other side."""
    drafted: int = 0
    """Somebody else spoke last and a draft of yours answers them: they wait on your push."""

    @property
    def yours(self) -> int:
        """The chains left to answer: somebody else spoke last and nothing of yours answers them yet."""
        return max(0, self.unresolved - self.theirs - self.drafted)

    def within(self, unresolved: int) -> ChainTurns:
        """These turns under Gerrit's count *unresolved*: that count stands, the others never exceed it."""
        theirs = min(self.theirs, unresolved)
        return ChainTurns(unresolved, theirs, min(self.drafted, unresolved - theirs))


def chain_turns(
    file_map: dict[str, list[dict[str, Any]]],
    me: int | None,
    drafts: dict[str, list[dict[str, Any]]] | None = None,
) -> ChainTurns:
    """The :class:`ChainTurns` of a Gerrit file map, your *drafts* (``GET …/drafts``) beside it.

    Without *me* every chain is yours: nothing tells whose turn it is. A chain of drafts only is
    not on Gerrit yet, so not counted.
    """
    chains = build_comment_chains(file_map, drafts, me=me)
    unresolved = [chain for chain in chains if not chain.resolved and any(not c.draft for c in chain.comments)]
    return ChainTurns(
        len(unresolved),
        sum(1 for chain in unresolved if chain.turn is ChainTurn.THEIRS),
        sum(1 for chain in unresolved if chain.turn is ChainTurn.DRAFT),
    )


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
    from gerrit_workflow_tools.core.gerrit.change_view import parse_gerrit_time
    from gerrit_workflow_tools.core.reviewer import format_gerrit_account_label

    raw_msg = comment.get("message")
    raw_id = comment.get("id")
    author_label: str | None = None
    raw_author = comment.get("author")
    author_id: int | None = None
    if isinstance(raw_author, dict):
        author_label = format_gerrit_account_label(raw_author)
        raw_author_id = raw_author.get("_account_id")
        author_id = raw_author_id if isinstance(raw_author_id, int) else None
    patch_set = comment.get("patch_set")
    line = _comment_line(comment)
    return InlineComment(
        path=path,
        line=line,
        message=raw_msg if isinstance(raw_msg, str) else "",
        comment_id=raw_id if isinstance(raw_id, str) else None,
        author=author_label,
        author_account_id=author_id,
        context_lines=_context_lines_from_raw(comment),
        patch_set=patch_set if isinstance(patch_set, int) else None,
        commit_id=commit_id if isinstance(commit_id := comment.get("commit_id"), str) else None,
        unresolved=comment.get("unresolved") is True,
        draft=draft,
        suggested_edits=suggested_edits_from_raw(path, comment, start_line=_comment_start_line(comment), line=line),
        updated=parse_gerrit_time(comment.get("updated")),
    )


def build_comment_chains(
    file_map: dict[str, list[dict[str, Any]]],
    drafts: dict[str, list[dict[str, Any]]] | None = None,
    *,
    me: int | None = None,
) -> list[CommentChain]:
    """Group Gerrit file-map comments into chains keyed by thread root.

    *drafts* (``GET …/drafts``) join the chain they reply to and sort after every
    published comment: publishing stamps them last, so they decide the state you will
    publish. ``resolved`` stays the published state. A chain of drafts only is your own
    unpublished comment. With *me*, your account id, each chain says whose turn it is.
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
        chain = CommentChain(
            root_id=root_id,
            path=root_path,
            line=_comment_line(root_comment),
            comments=tuple(_inline_comment_from_raw(p, c, draft=is_draft(c)) for p, c in items),
            resolved=_chain_resolved_from_raw(published or [c for _, c in items]),
            start_line=_comment_start_line(root_comment),
            on_parent=root_comment.get("side") == "PARENT",
        )
        chains.append(replace(chain, turn=chain_turn(chain, me)))

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
    me: int | None = None,
) -> list[CommentChain]:
    """Return comment chains filtered by *comments*, on their published state; with *me*, each with its turn.

    ``all`` lists unresolved chains first (path/line order within each group), then
    resolved ones. That keeps the attention-bearing threads at the top.
    """

    return select_chains(build_comment_chains(file_map, drafts, me=me), comments)


def gerrit_inline_comment_url(change_page_url: str | None, comment_id: str | None) -> str | None:
    """Build a PolyGerrit URL that opens the diff view focused on one inline comment.

    Matches the ``/c/<project>/+/<changeNum>/comment/<commentId>/`` route (see Gerrit
    ``gr-router`` COMMENT pattern). *comment_id* is the REST ``CommentInfo.id`` (URL-encoded
    UUID string).
    """
    if not change_page_url or not comment_id or not str(comment_id).strip():
        return None
    base = change_page_url.rstrip("/")
    cid_enc = quote(str(comment_id), safe="")
    return f"{base}/comment/{cid_enc}/"


_COMMENT_URL_RE = re.compile(r"/comment/([^/?#]+)")
_PATH_LINE_RE = re.compile(r"^(?P<path>.+):(?P<line>\d+)$")
_MIN_ID_PREFIX = 4
_T = TypeVar("_T")


@dataclass(frozen=True)
class ChainRef:
    """How a comment chain is named: any comment id in it or a unique prefix of one, ``PATH:LINE``
    (the one unresolved chain there), or the comment's Gerrit URL, which also names its change."""

    raw: str
    url: str | None = None
    """The comment URL as given: a changeish for its change."""
    path: str | None = None
    line: int | None = None

    @property
    def comment_id(self) -> str:
        """The id or id prefix it names; for ``PATH:LINE``, the text as written."""
        if self.url is not None:
            match = _COMMENT_URL_RE.search(self.url)
            return unquote(match.group(1)) if match else ""
        return self.raw


class ChainRefError(ValueError):
    """Text that cannot name a comment chain: a URL without ``/comment/<id>``."""


def parse_chain_ref(raw: str) -> ChainRef:
    """Read *raw* as a :class:`ChainRef`. Raises :class:`ChainRefError` for a URL that names no comment."""
    text = raw.strip()
    if "://" in text:
        if _COMMENT_URL_RE.search(text) is None:
            raise ChainRefError(f"not a comment URL (no /comment/<id>/): {text}")
        return ChainRef(text, url=text)
    located = _PATH_LINE_RE.fullmatch(text)
    if located:
        return ChainRef(text, path=located["path"], line=int(located["line"]))
    return ChainRef(text)


def _sits_at(chain: CommentChain, path: str, line: int) -> bool:
    on_path = chain.path == path or chain.path.endswith(f"/{path}")
    if not on_path or chain.line is None:
        return False
    return (chain.start_line or chain.line) <= line <= chain.line


def find_chains(candidates: Sequence[_T], ref: ChainRef, chain_of: Callable[[_T], CommentChain]) -> list[_T]:
    """The *candidates* whose chain *ref* names, each candidate holding one chain (``chain_of``).

    A comment id names its chain exactly. Else ``PATH:LINE`` names the unresolved chains there, or
    every chain there when none is unresolved. Else a unique id prefix (four characters at
    least): the chains holding a comment whose id starts with it. More than one is for the
    caller to report as ambiguous; none, as not found.
    """
    wanted = ref.comment_id

    def ids(item: _T) -> list[str]:
        return [c.comment_id for c in chain_of(item).comments if c.comment_id]

    exact = [item for item in candidates if wanted in ids(item)]
    if exact or ref.url is not None:
        return exact
    if ref.path is not None and ref.line is not None:
        path, line = ref.path, ref.line
        there = [item for item in candidates if _sits_at(chain_of(item), path, line)]
        open_there = [item for item in there if not chain_of(item).resolved]
        if there:
            return open_there or there
    if len(wanted) < _MIN_ID_PREFIX:
        return []
    return [item for item in candidates if any(i.startswith(wanted) for i in ids(item))]
