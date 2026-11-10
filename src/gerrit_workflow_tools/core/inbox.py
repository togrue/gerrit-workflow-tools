"""The inbox: its **inbox sections**, their queries, what a section adds up to, and the **headline**.

Host-scoped (ADR-0004): nothing here resolves a stack context or looks at local commits.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial

from gerrit_workflow_tools.core.attention import Attention
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.rest import parallel_map, self_account_id
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.review_chain import ChainMember, ReviewChain


DEFAULT_TO_REVIEW_QUERY = "is:open -is:wip -is:private -owner:self reviewer:self"
DEFAULT_TO_REVIEW_ALL_QUERY = "is:open -owner:self reviewer:self"
DEFAULT_OUTGOING_QUERY = "is:open owner:self"

TO_REVIEW = "to-review"
OUTGOING = "outgoing"

WAITING_ON_YOU = "waiting-on-you"
"""Headline reason for a *to review* chain; the other reasons are :class:`Attention` names."""

# What the author has to act on, most urgent first.
_HEADLINE_ORDER = (Attention.CI_FAILED, Attention.UNRESOLVED_COMMENTS, Attention.REVIEW_ISSUES)


@dataclass(frozen=True)
class InboxSection:
    """One **inbox section**: a Gerrit query and the review chains assembled from it."""

    name: str
    query: str
    chains: list[ReviewChain]


@dataclass(frozen=True)
class Headline:
    """The **headline**: the one thing across all inbox sections that most wants you."""

    section: str
    reason: str
    chain: ReviewChain
    member: ChainMember
    """The change to open."""


def build_to_review_query(
    settings: Settings,
    *,
    projects: list[str],
    include_unready: bool,
) -> str:
    """Section query for *to review*, with verified gate and project filters folded in."""
    custom = settings.inbox_to_review_query
    if custom:
        base = custom
    elif include_unready:
        base = DEFAULT_TO_REVIEW_ALL_QUERY
    else:
        base = DEFAULT_TO_REVIEW_QUERY
        if settings.inbox_require_verified:
            base = f"{base} label:{settings.inbox_verified_label}+1"
    return with_project_filter(base, list(projects) if projects else list(settings.inbox_projects))


def with_project_filter(query: str, projects: list[str]) -> str:
    """*query* narrowed to *projects*; unchanged when there are none."""
    if not projects:
        return query
    if len(projects) == 1:
        return f"{query} project:{projects[0]}"
    inner = " OR ".join(f"project:{name}" for name in projects)
    return f"{query} ({inner})"


def section_summary(chains: list[ReviewChain]) -> dict[str, int]:
    """Counts over one section's chains: sizes, oldest clocks, chains with failed CI or comments."""
    return {
        "chains": len(chains),
        "changes": sum(chain.depth for chain in chains),
        "oldest_unreviewed_seconds": max((chain.unreviewed_age_seconds for chain in chains), default=0),
        "oldest_wait_seconds": max((chain.wait_age_seconds for chain in chains), default=0),
        "ci_failures": sum(1 for chain in chains if Attention.CI_FAILED in chain.attention_reasons),
        "comments": sum(1 for chain in chains if Attention.UNRESOLVED_COMMENTS in chain.attention_reasons),
    }


def _urgency(chain: ReviewChain) -> int:
    """Position of the chain's most urgent reason in the headline order; past the end when it has none."""
    ranks = [i for i, reason in enumerate(_HEADLINE_ORDER) if reason in chain.attention_reasons]
    return min(ranks, default=len(_HEADLINE_ORDER))


def load_inbox(
    service: GerritService,
    *,
    now: datetime | None = None,
    projects: Sequence[str] = (),
    include_unready: bool = False,
    limit: int | None = None,
) -> list[InboxSection]:
    """Both inbox sections, queried in parallel: *to review*, then *outgoing* (your open chains).

    *To review* keeps the inbox order (longest unreviewed first) and honours *limit*, else
    ``ger.inbox.limit``. *Outgoing* puts what you must act on first, then the latest activity.
    """
    settings = service.settings
    moment = now if now is not None else datetime.now(timezone.utc)
    names = list(projects)
    queries = {
        TO_REVIEW: build_to_review_query(settings, projects=names, include_unready=include_unready),
        OUTGOING: with_project_filter(DEFAULT_OUTGOING_QUERY, names or list(settings.inbox_projects)),
    }
    account = self_account_id(service.rest)
    fetch = partial(service.fetch_review_chains, now=moment, self_account_id=account)
    to_review, outgoing = parallel_map(partial(fetch, query) for query in queries.values())
    cap = limit if limit is not None else settings.inbox_limit
    if cap is not None:
        to_review = to_review[: max(0, cap)]
    outgoing.sort(key=lambda chain: (_urgency(chain), chain.wait_age_seconds, chain.top.number))
    return [
        InboxSection(TO_REVIEW, queries[TO_REVIEW], to_review),
        InboxSection(OUTGOING, queries[OUTGOING], outgoing),
    ]


def pick_headline(sections: Sequence[InboxSection]) -> Headline | None:
    """The headline: failed CI, then unresolved comments, then a negative vote on an outgoing
    chain (its base-most such change); else the *to review* chain that has waited longest."""
    by_name = {section.name: section for section in sections}
    outgoing = by_name[OUTGOING].chains if OUTGOING in by_name else []
    for reason in _HEADLINE_ORDER:
        for chain in outgoing:
            for member in chain.members:
                if reason in member.attention_reasons:
                    return Headline(OUTGOING, reason, chain, member)
    to_review = by_name[TO_REVIEW].chains if TO_REVIEW in by_name else []
    if to_review:
        return Headline(TO_REVIEW, WAITING_ON_YOU, to_review[0], to_review[0].top)
    return None
