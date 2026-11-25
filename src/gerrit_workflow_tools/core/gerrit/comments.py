"""Inline comment chains of a change, published and draft, on :class:`GerritService`."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any, Literal

from gerrit_workflow_tools.core.comment_chains import (
    ChainRef,
    CommentSelection,
    collect_comment_chains,
    find_chains,
    parse_chain_ref,
)
from gerrit_workflow_tools.core.gerrit.cache_rows import comments_context_key, dict_rows
from gerrit_workflow_tools.core.gerrit.change_view import ChangeView
from gerrit_workflow_tools.core.gerrit.rest import LIGHT_QUERY_OPTIONS, parallel_map, printable_path
from gerrit_workflow_tools.core.gerrit.stack_context import ChangeResolutionError
from gerrit_workflow_tools.core.gerrit_change_status import CommentChain, InlineComment


if TYPE_CHECKING:
    from gerrit_workflow_tools.core.gerrit.service import GerritService


PUBLISH_GUARD_ENV = "GER_NO_PUBLISH"


def publish_blocked() -> bool:
    """True when :data:`PUBLISH_GUARD_ENV` forbids publishing drafts (agent environments set it).

    A guard against mistakes, not a security boundary: a process can change its own environment.
    """
    return os.environ.get(PUBLISH_GUARD_ENV, "").strip().lower() in ("1", "true", "yes")


@dataclass(frozen=True)
class ChainMatch:
    """A comment chain and the change it was found on."""

    change: str
    """The change's unique key (:meth:`GerritService.cache_key`), never a bare Change-Id."""
    chain: CommentChain


@dataclass(frozen=True)
class DraftReply:
    """What :meth:`CommentApi.save_reply` wrote."""

    action: Literal["created", "updated"]
    draft_id: str | None
    patch_set: int | None
    unresolved: bool
    """The chain's state once this draft is published."""
    replaced_message: str | None = None
    """Text of the draft this one replaced; ``None`` when it was created."""


class CommentApi:
    """Comment chains for one change: published comments through the cache, drafts live.

    Every public method takes a change as a triplet, a Gerrit id or a number. A bare Change-Id
    is narrowed once, on entry, to the push branch's change (:meth:`GerritService.cache_key`);
    without a stack context that raises ``ChangeResolutionError``. Gerrit is never asked by the
    bare Change-Id: that 404s ("Multiple changes found") once it is on a second branch.
    """

    def __init__(self, service: GerritService) -> None:
        self._service = service

    def _key(self, change_id: str) -> str:
        return self._service.cache_key(change_id)

    def get_chains(
        self,
        change_id: str,
        *,
        change_updated: str | None = None,
        context_padding: int | None = None,
        comments: CommentSelection = "unresolved",
    ) -> list[CommentChain]:
        """Comment chains of *change_id* selected by *comments*, with your drafts in their chains and
        whose turn each is (:attr:`CommentChain.turn`)."""
        return self._chains(
            self._key(change_id), change_updated=change_updated, context_padding=context_padding, comments=comments
        )

    def _chains(
        self,
        key: str,
        *,
        change_updated: str | None = None,
        context_padding: int | None = None,
        comments: CommentSelection = "unresolved",
    ) -> list[CommentChain]:
        # Two independent round trips (drafts are never cached): asked side by side, not one after the other.
        file_map, drafts = parallel_map(
            [
                partial(self._file_map, key, change_updated=change_updated, context_padding=context_padding),
                partial(self._drafts, key, context_padding=context_padding),
            ]
        )
        # Who you are says whose turn each chain is: remembered once asked, so no request when known.
        me = self._service.self_account_id() if any(file_map.values()) else None
        return collect_comment_chains(file_map, comments=comments, drafts=drafts, me=me)

    def drafted_changes(self, numbers: Sequence[int] | None = None, *, n: int = 25) -> list[ChangeView]:
        """The changes holding drafts of yours: one light ``has:draft`` query, among *numbers* when given.

        Drafts live in All-Users, so this is asked every time (never the cache). Without *numbers*,
        every change of yours on the host, at most *n*.
        """
        if numbers is not None and not numbers:
            return []
        among = f" ({' OR '.join(f'change:{number}' for number in numbers)})" if numbers else ""
        limit = len(numbers) if numbers else n
        rows = self._service.rest.query_changes(f"has:draft{among}", n=limit, options=list(LIGHT_QUERY_OPTIONS))
        return [ChangeView.from_payload(row) for row in dict_rows(rows)]

    def draft_counts(self, numbers: Sequence[int]) -> dict[int, int]:
        """How many drafts of yours each of *numbers* holds, those with none left out: the
        ``has:draft`` query, then a drafts GET only where it found some."""
        found = [view for view in self.drafted_changes(numbers) if view.number is not None]
        counts = {view.number: sum(map(len, self.get_drafts(view.key).values())) for view in found}
        return {number: counts[number] for number in numbers if counts.get(number)}

    def chains_with_drafts(self, change_id: str, *, change_updated: str | None = None) -> list[CommentChain]:
        """The chains of *change_id* that hold a draft of yours: what a publish would send."""
        chains = self.get_chains(change_id, change_updated=change_updated, comments="all")
        return [chain for chain in chains if chain.draft is not None]

    def drafts_to_publish(
        self, changes: Mapping[str, str | None], only: Sequence[str] | None, *, where: str
    ) -> dict[str, list[CommentChain]]:
        """Per change of *changes* (REST key -> freshness token) that has drafts of yours, the chains
        holding them; with *only*, the chains holding a draft that one of those ids names.

        An id is a draft's own id, or any comment id in its chain: the CHAIN that ``ger reply``
        answered names the draft it wrote there. An id naming none of your drafts on *where*
        raises before anything is sent.
        """
        pending = {
            key: chains
            for key, freshness in changes.items()
            if (chains := self.chains_with_drafts(key, change_updated=freshness))
        }
        if only is None:
            return pending
        wanted = {draft_id.strip() for draft_id in only}

        def ids(chain: CommentChain) -> set[str]:
            return {chain.root_id, *(c.comment_id for c in chain.comments if c.comment_id)}

        def drafted(chains: list[CommentChain]) -> list[CommentChain]:
            return [chain for chain in chains for c in chain.comments if c.draft]

        found = {i for chains in pending.values() for chain in drafted(chains) for i in ids(chain)}
        if missing := sorted(wanted - found):
            raise ChangeResolutionError(f"no draft of yours in a chain with id {', '.join(missing)} on {where}")
        narrowed = {key: [chain for chain in drafted(chains) if ids(chain) & wanted] for key, chains in pending.items()}
        return {key: chains for key, chains in narrowed.items() if chains}

    def chains_on(self, changes: Mapping[str, str | None]) -> list[ChainMatch]:
        """Every chain, published and draft, on *changes* (REST key -> freshness token)."""

        def on(change: str, freshness: str | None) -> list[ChainMatch]:
            return [
                ChainMatch(change, chain) for chain in self._chains(change, change_updated=freshness, comments="all")
            ]

        keyed = {self._key(change): freshness for change, freshness in changes.items()}
        found = parallel_map(partial(on, change, freshness) for change, freshness in keyed.items())
        return [match for per_change in found for match in per_change]

    def find_chain(self, changes: Mapping[str, str | None], ref: str | ChainRef) -> list[ChainMatch]:
        """Chains among *changes* that *ref* names (:func:`find_chains`): any comment id in a chain
        names the whole chain, root or reply, published or draft; so do ``PATH:LINE`` and an id prefix."""
        wanted = parse_chain_ref(ref) if isinstance(ref, str) else ref
        return find_chains(self.chains_on(changes), wanted, lambda match: match.chain)

    def save_reply(self, change_id: str, chain: CommentChain, message: str, *, unresolved: bool | None) -> DraftReply:
        """Write your reply to *chain* as a draft; update the draft you already have there.

        The draft answers the newest published comment, on that comment's patchset and
        side, as the web UI does. *unresolved* ``None`` keeps your existing draft's flag,
        else inherits the answered comment's: an open chain stays open.
        """
        answered, existing = chain.answered, chain.draft
        basis = existing or answered
        flag = basis.unresolved if unresolved is None else unresolved
        body: dict[str, Any] = {"path": chain.path, "message": message, "unresolved": flag}
        if answered.comment_id and not answered.draft:
            body["in_reply_to"] = answered.comment_id
        if chain.line is not None:
            body["line"] = chain.line
        if chain.on_parent:
            body["side"] = "PARENT"
        revision = basis.patch_set if basis.patch_set is not None else "current"
        row = self._service.rest.put_draft(
            self._key(change_id), revision, body, draft_id=existing.comment_id if existing else None
        )
        draft_id = row.get("id")
        return DraftReply(
            action="updated" if existing else "created",
            draft_id=draft_id if isinstance(draft_id, str) else None,
            patch_set=basis.patch_set,
            unresolved=flag,
            replaced_message=existing.message if existing else None,
        )

    def create_draft(self, change_id: str, patch_set: int, comment: Mapping[str, Any]) -> str | None:
        """Start a new chain: a draft *comment* (CommentInput fields) on *patch_set*; return its id."""
        row = self._service.rest.put_draft(self._key(change_id), patch_set, dict(comment))
        draft_id = row.get("id")
        return draft_id if isinstance(draft_id, str) else None

    def edit_draft(
        self, change_id: str, draft_id: str, message: str, *, unresolved: bool | None = None
    ) -> InlineComment | None:
        """Give your draft *draft_id* the text *message* and return it as it was; ``None`` if absent.

        Everything else it says goes back as Gerrit has it (place, side, what it answers), so
        the draft stays where it is. *unresolved* ``None`` keeps its flag.
        """
        key = self._key(change_id)
        for path, rows in self._drafts(key).items():
            for row in rows:
                if row.get("id") != draft_id:
                    continue
                flag = bool(row.get("unresolved")) if unresolved is None else unresolved
                body: dict[str, Any] = {"path": path, "message": message, "unresolved": flag}
                body.update({key: row[key] for key in ("line", "range", "side", "in_reply_to") if key in row})
                patch_set = row.get("patch_set")
                revision = patch_set if isinstance(patch_set, int) else "current"
                self._service.rest.put_draft(key, revision, body, draft_id=draft_id)
                line = row.get("line")
                return InlineComment(
                    path=path,
                    line=line if isinstance(line, int) else None,
                    message=str(row.get("message", "")),
                    comment_id=draft_id,
                    patch_set=patch_set if isinstance(patch_set, int) else None,
                    unresolved=bool(row.get("unresolved")),
                    draft=True,
                )
        return None

    def discard_draft(self, change_id: str, draft_id: str) -> InlineComment | None:
        """Delete your draft *draft_id* on any patchset of *change_id* and return it; ``None`` if absent."""
        key = self._key(change_id)
        for chain in self._chains(key, comments="all"):
            for comment in chain.comments:
                if comment.draft and comment.comment_id == draft_id:
                    revision = comment.patch_set if comment.patch_set is not None else "current"
                    self._service.rest.delete_draft(key, revision, draft_id)
                    return comment
        return None

    def discard_reply(self, change_id: str, chain: CommentChain) -> InlineComment:
        """Delete your draft in *chain* and return it. A chain without one is not found."""
        existing = chain.draft
        if existing is None or existing.comment_id is None:
            raise ChangeResolutionError(f"no draft of yours in the chain at {printable_path(chain.path)}")
        revision = existing.patch_set if existing.patch_set is not None else "current"
        self._service.rest.delete_draft(self._key(change_id), revision, existing.comment_id)
        return existing

    def publish_drafts(
        self,
        change_id: str,
        *,
        message: str | None = None,
        labels: Mapping[str, int] | None = None,
        draft_ids: Sequence[str] | None = None,
    ) -> None:
        """Publish your drafts on *change_id* (all, or *draft_ids*) with *labels* voted; drop its cached rows.

        Publishing moves the change's meta ref, but inside the trust window the probe is
        skipped; invalidating makes the next read see the published chains straight away.
        Callers check :func:`publish_blocked` first: the guard is policy, this is the act.
        """
        key = self._key(change_id)
        self._service.rest.publish_drafts(key, message=message, labels=labels, draft_ids=draft_ids)
        self._service.cache.invalidate_changes([key])

    def get_drafts(self, change_id: str) -> dict[str, list[dict[str, Any]]]:
        """Your drafts on every patchset of *change_id*, always fetched.

        Never cached: drafts live in All-Users, not in the change's NoteDb meta ref, so the
        follow-up freshness token (ADR-0007) does not move when a draft does.
        """
        return self._drafts(self._key(change_id))

    def _drafts(self, key: str, *, context_padding: int | None = None) -> dict[str, list[dict[str, Any]]]:
        return self._service.rest.list_drafts(key, context_padding=context_padding)

    def get_file_map(
        self,
        change_id: str,
        *,
        change_updated: str | None = None,
        context_padding: int | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Return raw published Gerrit comments grouped by file path."""
        return self._file_map(self._key(change_id), change_updated=change_updated, context_padding=context_padding)

    def _file_map(
        self, key: str, *, change_updated: str | None, context_padding: int | None
    ) -> dict[str, list[dict[str, Any]]]:
        rest = self._service.rest
        fetch = rest.get_comments
        if context_padding is not None:
            fetch = partial(rest.get_comments, enable_context=True, context_padding=context_padding)

        return self._service.cache.load_comments(
            key,
            fetch_comments=fetch,
            change_updated=change_updated,
            trust_window_seconds=self._service.trust_window_seconds,
            refresh=self._service.refresh,
            context_key=comments_context_key(context_padding),
        )
