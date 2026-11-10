"""JSON payloads for **review chains**, the **headline** (``ger inbox``, ``ger serve``) and the **review state**."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from gerrit_workflow_tools.core.attention import Attention
from gerrit_workflow_tools.core.inbox import Headline
from gerrit_workflow_tools.core.review_chain import ChainMember, ReviewChain, format_age
from gerrit_workflow_tools.core.review_checkout import ReviewState


def iso_utc(moment: datetime | None) -> str | None:
    """``2026-01-02T03:04:05Z`` for a UTC *moment*; ``None`` stays ``None``."""
    return moment.isoformat().replace("+00:00", "Z") if moment else None


def member_json(member: ChainMember) -> dict[str, object]:
    """One chain member: identity, votes, unresolved comments, attention."""
    return {
        "number": member.number,
        "change_id": member.change_id,
        "subject": member.subject,
        "verified": member.verified,
        "code_review": member.code_review,
        "comments_unresolved": member.comments_unresolved,
        "attention_reasons": list(member.attention_reasons),
        "url": member.url,
    }


def chain_json(chain: ReviewChain) -> dict[str, object]:
    """One review chain: its top, clocks, worst votes, and members base to top."""
    return {
        "key": chain.key,
        "top": {
            "number": chain.top.number,
            "change_id": chain.top.change_id,
            "subject": chain.top.subject,
            "url": chain.top.url,
        },
        "project": chain.project,
        "branch": chain.branch,
        "owner": {"name": chain.owner_name, "email": chain.owner_email},
        "depth": chain.depth,
        "wait_age_seconds": chain.wait_age_seconds,
        "unreviewed_age_seconds": chain.unreviewed_age_seconds,
        "last_activity": iso_utc(chain.last_activity),
        "verified": chain.verified,
        "code_review": chain.code_review,
        "comments_unresolved": chain.comments_unresolved,
        "attention_reasons": list(chain.attention_reasons),
        "partial_chain": chain.partial_chain,
        "members": [member_json(member) for member in chain.members],
    }


def review_state_json(state: ReviewState, *, chain: list[int] | None, url: str | None) -> dict[str, object]:
    """The review state, flat: the reviewed patchset's fields beside where and how it is checked out."""
    flat = {**asdict(state.change), **{k: v for k, v in asdict(state).items() if k != "change"}}
    return {**flat, "branch": state.branch, "chain": chain, "url": url}


def _headline_text(headline: Headline) -> str:
    member, chain = headline.member, headline.chain
    what = f"{member.number}: {member.subject}"
    if headline.reason == Attention.CI_FAILED:
        return f"CI failed on {what}"
    if headline.reason == Attention.UNRESOLVED_COMMENTS:
        count = member.comments_unresolved
        return f"{count} unresolved comment{'' if count == 1 else 's'} on {what}"
    if headline.reason == Attention.REVIEW_ISSUES:
        return f"Negative vote on {what}"
    return f"{chain.owner_name} has waited {format_age(chain.unreviewed_age_seconds)} for your review of {what}"


def headline_json(headline: Headline | None) -> dict[str, object] | None:
    """The **headline**: which change of which chain, why, and a sentence saying so."""
    if headline is None:
        return None
    return {
        "section": headline.section,
        "reason": headline.reason,
        "chain": headline.chain.key,
        "number": headline.member.number,
        "subject": headline.member.subject,
        "url": headline.member.url,
        "text": _headline_text(headline),
    }
