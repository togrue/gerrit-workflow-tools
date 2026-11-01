"""Review chains — Gerrit's relation chain, assembled from ChangeInfo payloads.

A **review chain** is a maximal set of open changes linked by current-revision
parent/child SHAs. Distinct from the **local stack**: this is derived from a
query result and never looks at local commits.

Pure functions over :class:`ChangeView` values, so unit tests drive them from
``ChangeStore`` rows without a host or a clone.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

from gerrit_workflow_tools.core.attention import Attention, ci_failed
from gerrit_workflow_tools.core.gerrit.change_view import ChangeView, parse_gerrit_time


# Query options that give parent SHAs, votes (with dates), and owner names.
INBOX_QUERY_OPTIONS = (
    "DETAILED_LABELS",
    "CURRENT_REVISION",
    "CURRENT_COMMIT",
    "DETAILED_ACCOUNTS",
    "SUBMITTABLE",
)

_OPEN_STATUSES = frozenset({"NEW", ""})


@dataclass(frozen=True)
class ChainMember:  # pylint: disable=too-many-instance-attributes
    """One open change in a review chain, oldest (base) to newest (top)."""

    number: int
    change_id: str
    subject: str
    project: str
    branch: str
    owner_name: str
    owner_email: str | None
    verified: int | None
    code_review: int | None
    comments_unresolved: int
    updated: datetime | None
    url: str | None
    attention_reasons: tuple[str, ...]


@dataclass(frozen=True)
class ReviewChain:  # pylint: disable=too-many-instance-attributes
    """One review unit: the chain you decide to start or not start."""

    key: str
    top: ChainMember
    members: tuple[ChainMember, ...]
    depth: int
    owner_name: str
    owner_email: str | None
    project: str
    branch: str
    wait_age_seconds: int
    unreviewed_age_seconds: int
    last_activity: datetime | None
    verified: int | None
    code_review: int | None
    comments_unresolved: int
    attention_reasons: tuple[str, ...]
    partial_chain: bool
    url: str | None


def format_age(seconds: int) -> str:
    """Compact age for the inbox: ``12m``, ``4h``, ``3d``."""
    age = max(0, int(seconds))
    minutes = age // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h"
    return f"{hours // 24}d"


def host_from_web_base(web_base: str) -> str:
    """Hostname of a Gerrit web base, used as the chain-key prefix."""
    parsed = urlparse(web_base.rstrip("/"))
    host = parsed.netloc or parsed.path
    return host.split("@", 1)[-1].split(":", 1)[0] or "unknown"


def missing_parent_shas(changes: list[ChangeView]) -> list[str]:
    """First-parent SHAs that are not another change's current revision."""
    known = {change.current_revision for change in changes if change.current_revision}
    parents = (change.first_parent for change in changes if change.first_parent)
    return list(dict.fromkeys(parent for parent in parents if parent not in known))


def _self_vote_date(change: ChangeView, self_account_id: int | None) -> datetime | None:
    if self_account_id is None:
        return None
    dates = [vote.date for vote in change.code_review_votes if vote.account_id == self_account_id and vote.date]
    return max(dates, default=None)


def member_unreviewed_since(change: ChangeView, self_account_id: int | None) -> datetime | None:
    """When this change last started waiting for *self*'s review, or ``None`` if current.

    Prefers the attention-set clock (Gerrit's "your turn"). Falls back to "current
    patch set uploaded after my last Code-Review vote", then to change creation
    when I have never voted.
    """
    attention_at = change.attention_set.get(self_account_id) if self_account_id is not None else None
    if attention_at is not None:
        return attention_at
    last_vote = _self_vote_date(change, self_account_id)
    patch_created = change.current_revision_created()
    if last_vote is None:
        return patch_created or parse_gerrit_time(change.created)
    if patch_created is not None and last_vote < patch_created:
        return patch_created
    return None


def member_attention_reasons(change: ChangeView) -> tuple[str, ...]:
    """Inbox member reasons — CI, comments, negative vote. No local patchset states."""
    reasons: list[str] = []
    if ci_failed(change.verified):
        reasons.append(Attention.CI_FAILED)
    if change.unresolved_comments:
        reasons.append(Attention.UNRESOLVED_COMMENTS)
    if change.code_review is not None and change.code_review < 0:
        reasons.append(Attention.REVIEW_ISSUES)
    return tuple(reasons)


def _worst_label(values: list[int | None]) -> int | None:
    known = [value for value in values if value is not None]
    return min(known) if known else None


def _to_member(change: ChangeView, web_base: str) -> ChainMember | None:
    if change.number is None:
        return None
    return ChainMember(
        number=change.number,
        change_id=change.change_id,
        subject=change.subject,
        project=change.project,
        branch=change.branch,
        owner_name=change.owner_name,
        owner_email=change.owner_email,
        verified=change.verified,
        code_review=change.code_review,
        comments_unresolved=change.unresolved_comments or 0,
        updated=parse_gerrit_time(change.updated),
        url=change.url(web_base),
        attention_reasons=member_attention_reasons(change),
    )


def _index_payloads(changes: list[ChangeView]) -> dict[str, ChangeView]:
    """Key changes by Gerrit ``id``; last write wins for a duplicate id."""
    return {change.key: change for change in changes if change.key}


class _UnionFind:
    """Disjoint-set of change ids used while grouping a query result into chains."""

    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def add(self, item: str) -> None:
        """Ensure *item* is a set of its own."""
        self._parent.setdefault(item, item)

    def find(self, item: str) -> str:
        """Return the representative for *item*'s set."""
        parent = self._parent[item]
        if parent != item:
            parent = self.find(parent)
            self._parent[item] = parent
        return parent

    def union(self, left: str, right: str) -> None:
        """Merge the sets containing *left* and *right*."""
        root_left, root_right = self.find(left), self.find(right)
        if root_left != root_right:
            self._parent[root_right] = root_left


def _parent_id(change: ChangeView, sha_to_id: dict[str, str]) -> str | None:
    return sha_to_id.get(change.first_parent) if change.first_parent else None


def _order_members(
    component: set[str],
    payloads: dict[str, ChangeView],
    sha_to_id: dict[str, str],
) -> list[str]:
    """Base-to-top order along first-parent links; siblings by change number."""
    children: dict[str, list[str]] = defaultdict(list)
    bases: list[str] = []
    for member_id in component:
        parent_id = _parent_id(payloads[member_id], sha_to_id)
        if parent_id and parent_id in component:
            children[parent_id].append(member_id)
        else:
            bases.append(member_id)
    for child_ids in children.values():
        child_ids.sort(key=lambda item: payloads[item].number or 0)
    bases.sort(key=lambda item: payloads[item].number or 0)
    ordered: list[str] = []
    seen: set[str] = set()

    def _walk(member_id: str) -> None:
        if member_id in seen:
            return
        seen.add(member_id)
        ordered.append(member_id)
        for child_id in children.get(member_id, []):
            _walk(child_id)

    for base_id in bases:
        _walk(base_id)
    for member_id in sorted(component, key=lambda item: payloads[item].number or 0):
        _walk(member_id)
    return ordered


def _pick_top(
    ordered_ids: list[str],
    component: set[str],
    payloads: dict[str, ChangeView],
    sha_to_id: dict[str, str],
) -> str:
    """Member whose current revision is not the parent of any other member."""
    parent_ids = {_parent_id(payloads[member_id], sha_to_id) for member_id in component}
    tips = [member_id for member_id in ordered_ids if member_id not in parent_ids]
    if not tips:
        return ordered_ids[-1]
    return max(tips, key=lambda item: payloads[item].number or 0)


def _seconds_since(moment: datetime | None, now: datetime) -> int:
    """Seconds from *moment* to *now*, floored at zero. Missing *moment* is zero."""
    if moment is None:
        return 0
    return max(0, int((now - moment).total_seconds()))


def _chain_from_component(  # pylint: disable=too-many-arguments,too-many-locals
    component: set[str],
    payloads: dict[str, ChangeView],
    sha_to_id: dict[str, str],
    *,
    web_base: str,
    host: str,
    now: datetime,
    self_account_id: int | None,
    unmatched: set[str],
) -> ReviewChain | None:
    """Build one ReviewChain from a connected component, or None if it has no numbers."""
    ordered_ids = _order_members(component, payloads, sha_to_id)
    members = [member for member_id in ordered_ids if (member := _to_member(payloads[member_id], web_base))]
    if not members:
        return None
    top_id = _pick_top(ordered_ids, component, payloads, sha_to_id)
    top_number = payloads[top_id].number
    top_member = next((member for member in members if member.number == top_number), members[-1])
    last_activity = max((member.updated for member in members if member.updated is not None), default=None)
    stamps = (member_unreviewed_since(payloads[member_id], self_account_id) for member_id in ordered_ids)
    unreviewed_at = min((stamp for stamp in stamps if stamp is not None), default=None)
    project = top_member.project
    return ReviewChain(
        key=f"{host}~{project}~{top_member.number}",
        top=top_member,
        members=tuple(members),
        depth=len(members),
        owner_name=top_member.owner_name,
        owner_email=top_member.owner_email,
        project=project,
        branch=top_member.branch,
        wait_age_seconds=_seconds_since(last_activity, now),
        unreviewed_age_seconds=_seconds_since(unreviewed_at, now),
        last_activity=last_activity,
        verified=_worst_label([member.verified for member in members]),
        code_review=_worst_label([member.code_review for member in members]),
        comments_unresolved=sum(member.comments_unresolved for member in members),
        attention_reasons=tuple(dict.fromkeys(reason for member in members for reason in member.attention_reasons)),
        partial_chain=any(payloads[member_id].first_parent in unmatched for member_id in component),
        url=top_member.url,
    )


def assemble_review_chains(  # pylint: disable=too-many-arguments,too-many-locals
    queried: list[ChangeView],
    follow_up: list[ChangeView],
    *,
    web_base: str,
    now: datetime,
    self_account_id: int | None,
    follow_up_unmatched: set[str] | None = None,
) -> list[ReviewChain]:
    """Assemble review chains from a query result plus optional parent follow-up rows.

    *queried* members seed chains even when you are not a reviewer on every
    follow-up row — those extra open changes are context. Merged/abandoned
    follow-up rows are ignored (they are the ground the chain sits on).
    *follow_up_unmatched* are parent SHAs that a follow-up returned for a
    non-current revision: the chain is then flagged ``partial_chain``.
    """
    queried_ids = {change.key for change in queried if change.key}
    open_follow_up = [change for change in follow_up if change.is_open and change.key not in queried_ids]
    payloads = _index_payloads(list(queried) + open_follow_up)
    if not payloads:
        return []

    sha_to_id = {change.current_revision: key for key, change in payloads.items() if change.current_revision}

    union = _UnionFind()
    for member_id in payloads:
        union.add(member_id)
    for member_id, change in payloads.items():
        parent_id = _parent_id(change, sha_to_id)
        if parent_id:
            union.union(member_id, parent_id)

    groups: dict[str, set[str]] = defaultdict(set)
    for member_id in payloads:
        groups[union.find(member_id)].add(member_id)

    unmatched = follow_up_unmatched or set()
    host = host_from_web_base(web_base)
    chains: list[ReviewChain] = []
    for component in groups.values():
        chain = _chain_from_component(
            component,
            payloads,
            sha_to_id,
            web_base=web_base,
            host=host,
            now=now,
            self_account_id=self_account_id,
            unmatched=unmatched,
        )
        if chain is not None:
            chains.append(chain)
    chains.sort(key=lambda chain: (-chain.unreviewed_age_seconds, -chain.wait_age_seconds, chain.top.number))
    return chains
