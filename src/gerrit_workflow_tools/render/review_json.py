"""JSON payloads for **review chains**, **inbox sections** and the **headline** (``ger inbox``, ``ger serve``),
and the **review state**."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from gerrit_workflow_tools.core.attention import Attention, attention_severity
from gerrit_workflow_tools.core.gerrit_change_status import CommentChain, InlineComment
from gerrit_workflow_tools.core.inbox import Headline, InboxSection, RebaseHeadline, section_summary
from gerrit_workflow_tools.core.rebase_state import RebaseStop
from gerrit_workflow_tools.core.review_chain import ChainMember, ReviewChain, format_age
from gerrit_workflow_tools.core.review_checkout import ReviewState
from gerrit_workflow_tools.core.review_target import VoteOption, VotePlan
from gerrit_workflow_tools.render.comments import chain_location, draft_transition, drafts_in, first_line
from gerrit_workflow_tools.render.status_fmt import unresolved_comments_text


def iso_utc(moment: datetime | None) -> str | None:
    """``2026-01-02T03:04:05Z`` for a UTC *moment*; ``None`` stays ``None``."""
    return moment.isoformat().replace("+00:00", "Z") if moment else None


def member_json(member: ChainMember) -> dict[str, object]:
    """One chain member: identity, current patch set, votes, unresolved comments, attention."""
    return {
        "number": member.number,
        "change_id": member.change_id,
        "subject": member.subject,
        "verified": member.verified,
        "code_review": member.code_review,
        "comments_unresolved": member.comments_unresolved,
        "comments_awaiting_reply": member.comments_awaiting_reply,
        "attention_reasons": list(member.attention_reasons),
        "severity": attention_severity(member.attention_reasons).value,
        "url": member.url,
        "sha": member.sha,
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
        "last_activity_yours": chain.last_activity_yours,
        "verified": chain.verified,
        "code_review": chain.code_review,
        "comments_unresolved": chain.comments_unresolved,
        "comments_awaiting_reply": chain.comments_awaiting_reply,
        "attention_reasons": list(chain.attention_reasons),
        "severity": attention_severity(chain.attention_reasons).value,
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
        yours = member.comments_unresolved - member.comments_awaiting_reply
        return f"{unresolved_comments_text(yours)} on {what}"
    if headline.reason == Attention.REVIEW_ISSUES:
        return f"Negative vote on {what}"
    return f"{chain.owner_name} has waited {format_age(chain.unreviewed_age_seconds)} for your review of {what}"


# A rebase underway, by why it stopped, around the commit it is at.
_REBASE_STOPS = {
    RebaseStop.EDIT: "Rebase stopped at {}",
    RebaseStop.SPLIT: "Rebase stopped to split {}",
    RebaseStop.CONFLICT: "Conflict in {}",
    RebaseStop.RESOLVED: "Conflict resolved in {}",
    RebaseStop.PAUSED: "Rebase paused at {}",
}


def _rebase_headline_text(headline: RebaseHeadline) -> str:
    commit, stop = headline.commit, headline.stop
    if commit is None:
        return "Rebase paused" if stop is RebaseStop.PAUSED else "Rebase stopped" if stop else "Rebase underway"
    # A subject alone is quoted, so "Conflict in" does not read as naming a file.
    what = f"{commit.number}: {commit.summary}" if commit.number else f'"{commit.summary}"'
    folding = " (folding a fixup into it)" if headline.folding else ""
    return (_REBASE_STOPS[stop] if stop else "Rebase underway at {}").format(what) + folding


def _rebase_headline_json(headline: RebaseHeadline) -> dict[str, object]:
    commit = headline.commit
    return {
        "section": headline.section,
        "reason": headline.reason,
        "severity": headline.severity.value,
        "stop": headline.stop.value if headline.stop else None,
        "chain": None,
        "number": commit.number if commit else None,
        "subject": commit.summary if commit else None,
        "sha": commit.sha if commit else None,
        "url": commit.gerrit_url if commit else None,
        "folding": headline.folding,
        "text": _rebase_headline_text(headline),
    }


def sections_json(sections: list[InboxSection]) -> list[dict[str, object]]:
    """The **inbox sections**, ``ger inbox --json`` and ``GET /overview`` alike: name, query, summary, chains."""
    return [
        {
            "name": section.name,
            "query": section.query,
            "summary": section_summary(section.chains),
            "chains": [chain_json(chain) for chain in section.chains],
        }
        for section in sections
    ]


def headline_json(headline: Headline | RebaseHeadline | None) -> dict[str, object] | None:
    """The **headline**: which change of which chain, why, and a sentence saying so.

    A rebase underway is the checkout's (``section`` ``local``, ``reason`` ``rebase``): no
    chain, ``stop`` as ``GET /stack`` gives it, and the commit it stopped at, when it is at one.
    """
    if headline is None:
        return None
    if isinstance(headline, RebaseHeadline):
        return _rebase_headline_json(headline)
    return {
        "section": headline.section,
        "reason": headline.reason,
        "severity": headline.severity.value,
        "chain": headline.chain.key,
        "number": headline.member.number,
        "subject": headline.member.subject,
        "url": headline.member.url,
        "text": _headline_text(headline),
    }


def _draft_json(chain: CommentChain, draft: InlineComment) -> dict[str, object]:
    """One draft a vote publishes: where (``path:line``), its first line, whether it resolves or reopens its chain."""
    transition = draft_transition(chain, draft.unresolved).removeprefix(" -> ") if draft is chain.draft else ""
    return {
        "path": chain.path,
        "line": chain.line,
        "location": chain_location(chain),
        "text": first_line(draft.message),
        "transition": transition or None,
    }


def vote_plan_json(plan: VotePlan, options: list[VoteOption], *, web_base: str) -> dict[str, object]:
    """``GET /vote``: per change what goes out (its drafts) and what you may vote there; the votes on offer."""
    changes = [
        {
            "number": target.number,
            "change": target.change.key,
            "subject": target.change.subject,
            "patch_set": target.change.current_revision_number,
            "url": target.change.url(web_base),
            "owner": target.change.owner_name,
            "own": target.own,
            "permitted": {label: list(values) for label, values in target.change.permitted_votes.items()},
            "ruled_out": list(target.ruled_out),
            "drafts": [_draft_json(chain, draft) for chain, draft in drafts_in(list(target.drafts))],
        }
        for target in plan.targets
    ]
    return {
        "changes": changes,
        "votes": dict(plan.votes),
        "drafts": plan.draft_count,
        "options": [{**asdict(option), "takes": list(option.takes)} for option in options],
    }


def sent_vote_json(plan: VotePlan, status: str) -> dict[str, list[dict[str, object]]]:
    """``ger review --vote --json`` and ``POST /vote``: each change the vote went to (*status*), each it skipped."""
    rows: list[dict[str, object]] = [
        {
            "change": target.change.key,
            "number": target.number,
            "drafts": target.draft_count,
            "labels": dict(plan.votes),
            "message": plan.message,
            "status": status,
        }
        for target in plan.takes
    ]
    skipped: list[dict[str, object]] = [
        {"change": t.change.key, "number": t.number, "ruled_out": list(t.ruled_out)} for t in plan.skipped
    ]
    return {"changes": rows, "skipped": skipped}
