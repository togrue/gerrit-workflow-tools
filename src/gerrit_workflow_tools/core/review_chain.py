"""Review chains — Gerrit's relation chain, assembled from ChangeInfo payloads.

A **review chain** is a maximal set of open changes linked by current-revision
parent/child SHAs. Distinct from the **local stack**: this is derived from a
query result and never looks at local commits.

Pure functions over :class:`ChangeView` values, so unit tests drive them from
``ChangeStore`` rows without a host or a clone.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

from gerrit_workflow_tools.core.attention import Attention, ci_failed, comment_reason
from gerrit_workflow_tools.core.gerrit.change_view import ChangeView, parse_gerrit_time
from gerrit_workflow_tools.core.review_chain_graph import ChainComponent, chain_components


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
    sha: str | None = None
    """The commit of the current patch set, when Gerrit sent it."""
    work_in_progress: bool = False
    private: bool = False
    updated_by_you: bool = False
    """Your upload or your vote is its last update (:meth:`ChangeView.updated_by`): no news for you."""
    comments_awaiting_reply: int = 0
    """Of :attr:`comments_unresolved`, the chains whose last published comment is yours: they wait
    on the other side (``ChainTurn.THEIRS``)."""
    your_turn: bool = False
    """It is your turn to review it (:func:`your_turn_since`); never on a change of yours."""


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
    last_activity_yours: bool = False
    """The member updated last was updated by you (your push, your vote): not news to you."""
    comments_awaiting_reply: int = 0
    """Of :attr:`comments_unresolved`, the chains that wait on the other side, over all members."""
    your_turn: bool = False
    """It is your turn to review a member: the chain waits on you. Otherwise a chain you review
    waits on its author; :attr:`unreviewed_age_seconds` is then 0."""

    @property
    def work_in_progress(self) -> bool:
        """Whether a member is WIP: nobody reviews the chain until you mark it ready."""
        return any(member.work_in_progress for member in self.members)

    @property
    def private(self) -> bool:
        """Whether a member is private."""
        return any(member.private for member in self.members)


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


def _self_vote_date(change: ChangeView, self_account_id: int) -> datetime | None:
    dates = [vote.date for vote in change.code_review_votes if vote.account_id == self_account_id and vote.date]
    return max(dates, default=None)


def your_turn_since(change: ChangeView, self_account_id: int | None) -> datetime | None:
    """Since when it is your **turn** on somebody else's *change*; ``None`` while it is not. Never
    on a change of yours. The one place that decides it.

    Gerrit's attention set decides wherever it holds you: in it, your turn since Gerrit put you
    there (an upload, a reply to you); taken out of it (your reply), not yours. With neither, a
    server with the attention set switched off or one that never had you in it: your turn while
    the current patch set came after your last Code-Review vote, or since the change was created
    when you never voted.
    """
    me = self_account_id
    if me is None or change.owned_by(me):
        return None
    created = parse_gerrit_time(change.created)
    if me in change.attention_set:
        return change.attention_set[me] or change.current_revision_created() or created
    if me in change.removed_from_attention_set:
        return None
    last_vote = _self_vote_date(change, me)
    patch_created = change.current_revision_created()
    if last_vote is None:
        return patch_created or created
    if patch_created is not None and last_vote < patch_created:
        return patch_created
    return None


def member_attention_reasons(change: ChangeView, awaiting_reply: int = 0) -> tuple[str, ...]:
    """Inbox member reasons — CI, comments, negative vote. No local patchset states.

    *awaiting_reply* of its unresolved comment chains wait on the other side
    (:func:`~gerrit_workflow_tools.core.attention.comment_reason` decides as for a commit).
    """
    reasons: list[str] = []
    if ci_failed(change.verified):
        reasons.append(Attention.CI_FAILED)
    if comments := comment_reason(change.unresolved_comments or 0, awaiting_reply):
        reasons.append(comments)
    if change.code_review is not None and change.code_review < 0:
        reasons.append(Attention.REVIEW_ISSUES)
    return tuple(reasons)


def _worst_label(values: list[int | None]) -> int | None:
    """The worst vote of a label over a chain's members: a negative one; else a missing one
    (``None``: a change nobody voted on is not approved, whatever the others have); else the lowest."""
    known = [value for value in values if value is not None]
    if any(value < 0 for value in known) or len(known) == len(values):
        return min(known, default=None)
    return None


def _to_member(
    change: ChangeView, web_base: str, self_account_id: int | None = None, awaiting_reply: int = 0
) -> ChainMember | None:
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
        attention_reasons=member_attention_reasons(change, awaiting_reply),
        sha=change.current_revision,
        work_in_progress=change.work_in_progress,
        private=change.private,
        updated_by_you=change.updated_by(self_account_id),
        comments_awaiting_reply=awaiting_reply,
        your_turn=your_turn_since(change, self_account_id) is not None,
    )


def _seconds_since(moment: datetime | None, now: datetime) -> int:
    """Seconds from *moment* to *now*, floored at zero. Missing *moment* is zero."""
    if moment is None:
        return 0
    return max(0, int((now - moment).total_seconds()))


def _chain_from_component(  # pylint: disable=too-many-arguments,too-many-locals
    component: ChainComponent,
    payloads: Mapping[str, ChangeView],
    *,
    web_base: str,
    host: str,
    now: datetime,
    self_account_id: int | None,
    unmatched: set[str],
    awaiting_reply: Mapping[str, int],
) -> ReviewChain | None:
    """Build one ReviewChain from a connected component, or None if it has no numbers."""
    ordered_ids = component.ordered
    members = [
        member
        for member_id in ordered_ids
        if (member := _to_member(payloads[member_id], web_base, self_account_id, awaiting_reply.get(member_id, 0)))
    ]
    if not members:
        return None
    top_number = payloads[component.top].number
    top_member = next((member for member in members if member.number == top_number), members[-1])
    last_activity = max((member.updated for member in members if member.updated is not None), default=None)
    last = [member for member in members if member.updated == last_activity]
    stamps = (your_turn_since(payloads[member_id], self_account_id) for member_id in ordered_ids)
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
        comments_awaiting_reply=sum(member.comments_awaiting_reply for member in members),
        attention_reasons=tuple(dict.fromkeys(reason for member in members for reason in member.attention_reasons)),
        partial_chain=any(payloads[member_id].first_parent in unmatched for member_id in ordered_ids),
        url=top_member.url,
        last_activity_yours=last_activity is not None and all(member.updated_by_you for member in last),
        your_turn=unreviewed_at is not None,
    )


def assemble_review_chains(  # pylint: disable=too-many-arguments,too-many-locals
    queried: list[ChangeView],
    follow_up: list[ChangeView],
    *,
    web_base: str,
    now: datetime,
    self_account_id: int | None,
    follow_up_unmatched: set[str] | None = None,
    awaiting_reply: Mapping[str, int] | None = None,
) -> list[ReviewChain]:
    """Assemble review chains from a query result plus optional parent follow-up rows.

    *queried* members seed chains even when you are not a reviewer on every
    follow-up row — those extra open changes are context. Merged/abandoned
    follow-up rows are ignored (they are the ground the chain sits on).
    *follow_up_unmatched* are parent SHAs that a follow-up returned for a
    non-current revision: the chain is then flagged ``partial_chain``. *awaiting_reply* says, by
    change key, how many of its unresolved comment chains wait on the other side.
    """
    queried_ids = {change.key for change in queried if change.key}
    open_follow_up = [change for change in follow_up if change.is_open and change.key not in queried_ids]
    payloads = {change.key: change for change in [*queried, *open_follow_up] if change.key}  # last one wins
    unmatched = follow_up_unmatched or set()
    host = host_from_web_base(web_base)
    chains: list[ReviewChain] = []
    for component in chain_components(payloads):
        chain = _chain_from_component(
            component,
            payloads,
            web_base=web_base,
            host=host,
            now=now,
            self_account_id=self_account_id,
            unmatched=unmatched,
            awaiting_reply=awaiting_reply or {},
        )
        if chain is not None:
            chains.append(chain)
    # Your turn first, longest waited first; then the chains that wait on their author.
    chains.sort(
        key=lambda chain: (
            not chain.your_turn,
            -chain.unreviewed_age_seconds,
            -chain.wait_age_seconds,
            chain.top.number,
        )
    )
    return chains
