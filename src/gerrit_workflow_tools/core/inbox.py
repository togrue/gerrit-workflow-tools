"""The inbox: its **inbox sections**, their queries, what a section adds up to, and the **headline**.

Host-scoped (ADR-0004): nothing here resolves a stack context or looks at local commits. Run
inside a clone, the sections are narrowed to that clone's Gerrit project (:func:`project_scope`).
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from pathlib import Path

from gerrit_workflow_tools.core.attention import Attention, Severity, attention_severity
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.rest import norm_change_id, parallel_map
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.gerrit_project_id import resolve_gerrit_project_name
from gerrit_workflow_tools.core.review_chain import ChainMember, ReviewChain


DEFAULT_TO_REVIEW_QUERY = "is:open -is:wip -is:private -owner:self reviewer:self"
DEFAULT_TO_REVIEW_ALL_QUERY = "is:open -owner:self reviewer:self"
DEFAULT_OUTGOING_QUERY = "is:open owner:self"

TO_REVIEW = "to-review"
OUTGOING = "outgoing"

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

    @property
    def severity(self) -> Severity:
        """How urgent the reason is, as core rates every reason."""
        return attention_severity([self.reason])


def build_to_review_query(
    settings: Settings,
    *,
    projects: list[str],
    include_unready: bool,
) -> str:
    """Section query for *to review*, with verified gate and project filters folded in.

    *projects* is the scope as :func:`project_scope` decides it; empty means the whole host.
    """
    custom = settings.inbox_to_review_query
    if custom:
        base = custom
    elif include_unready:
        base = DEFAULT_TO_REVIEW_ALL_QUERY
    else:
        base = DEFAULT_TO_REVIEW_QUERY
        if settings.inbox_require_verified:
            base = f"{base} label:{settings.inbox_verified_label}+1"
    return with_project_filter(base, list(projects))


def to_review_needs_you_as_reviewer(settings: Settings) -> bool:
    """Whether a change must list you as a reviewer to match the *to review* query: true of the
    built-in queries (``reviewer:self``), not of a custom ``inbox.toReviewQuery``, which may
    select changes any other way.
    """
    return not settings.inbox_to_review_query


def waiting_on_your_review(service: GerritService, numbers: Collection[int]) -> set[int]:
    """Which of the changes *numbers* wait on your review: the ones the *to review* section's own
    query (:func:`build_to_review_query`) matches, in any project, with no limit and without
    ``--unready``. One query; none for no *numbers*.

    The Change view asks it, so that a change listed under *to review* says so there too. The list
    is narrower (the clone's project, ``inbox.limit``): a change of another project, or beyond the
    limit, waits on you without being listed.
    """
    if not numbers:
        return set()
    query = build_to_review_query(service.settings, projects=[], include_unready=False)
    changes = " OR ".join(f"change:{number}" for number in sorted(set(numbers)))
    found = {row.get("_number") for row in service.changes.query(f"({query}) ({changes})")}
    return {number for number in numbers if number in found}


def project_scope(
    cwd: Path | str | None,
    settings: Settings,
    *,
    projects: Sequence[str] = (),
    all_projects: bool = False,
) -> list[str]:
    """The Gerrit projects the inbox is narrowed to; empty means every project on the host.

    Projects named explicitly win. Otherwise ``all_projects`` asks for the whole host; inside a
    clone it is the clone's own project; outside one, ``inbox.projects``.
    """
    if projects:
        return list(projects)
    if all_projects:
        return []
    current = resolve_gerrit_project_name(cwd, settings=settings)
    if current:
        return [current]
    return list(settings.inbox_projects)


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


def _touches(chain: ReviewChain, change_ids: Collection[str]) -> bool:
    """Whether a member of *chain* carries one of *change_ids*."""
    return any(norm_change_id(member.change_id) in change_ids for member in chain.members)


def _urgency(chain: ReviewChain) -> int:
    """Position of the chain's most urgent reason in the headline order; past the end when it has none."""
    ranks = [i for i, reason in enumerate(_HEADLINE_ORDER) if reason in chain.attention_reasons]
    return min(ranks, default=len(_HEADLINE_ORDER))


def load_inbox(
    service: GerritService,
    *,
    now: datetime | None = None,
    projects: Sequence[str] = (),
    all_projects: bool = False,
    include_unready: bool = False,
    limit: int | None = None,
    sections: Sequence[str] = (TO_REVIEW, OUTGOING),
) -> list[InboxSection]:
    """The wanted inbox *sections*, queried in parallel: *to review*, then *outgoing* (your open
    chains). A section not wanted is not asked for.

    Both are narrowed to :func:`project_scope` of the service's directory. *To review* keeps the
    inbox order (longest unreviewed first) and honours *limit*, else ``ger.inbox.limit``.
    *Outgoing* puts what you must act on first, then the latest activity.
    """
    settings = service.settings
    moment = now if now is not None else datetime.now(timezone.utc)
    names = project_scope(service.cwd, settings, projects=projects, all_projects=all_projects)
    every = {
        TO_REVIEW: lambda: build_to_review_query(settings, projects=names, include_unready=include_unready),
        OUTGOING: lambda: with_project_filter(DEFAULT_OUTGOING_QUERY, names),
    }
    queries = {name: build() for name, build in every.items() if name in sections}
    if not queries:
        return []
    account = service.self_account_id()
    fetch = partial(service.fetch_review_chains, now=moment, self_account_id=account)
    fetched = dict(zip(queries, parallel_map(partial(fetch, query) for query in queries.values()), strict=True))
    cap = limit if limit is not None else settings.inbox_limit
    if cap is not None and TO_REVIEW in fetched:
        fetched[TO_REVIEW] = fetched[TO_REVIEW][: max(0, cap)]
    if OUTGOING in fetched:
        fetched[OUTGOING].sort(key=lambda chain: (_urgency(chain), chain.wait_age_seconds, chain.top.number))
    return [InboxSection(name, queries[name], chains) for name, chains in fetched.items()]


def pick_headline(sections: Sequence[InboxSection], *, current: Collection[str] | None = None) -> Headline | None:
    """The headline: failed CI, then unresolved comments, then a negative vote on an outgoing
    chain (its base-most such change); else the *to review* chain that has waited longest.

    *current* is the Change-Ids of the chain you work on (normalised, as
    :func:`norm_change_id` gives them). Given, failed CI is a headline only on a chain with one
    of them: another chain's failure waits until you look at it.
    """
    by_name = {section.name: section for section in sections}
    outgoing = by_name[OUTGOING].chains if OUTGOING in by_name else []
    for reason in _HEADLINE_ORDER:
        for chain in outgoing:
            if reason == Attention.CI_FAILED and current is not None and not _touches(chain, current):
                continue
            for member in chain.members:
                if reason in member.attention_reasons:
                    return Headline(OUTGOING, reason, chain, member)
    to_review = by_name[TO_REVIEW].chains if TO_REVIEW in by_name else []
    if to_review:
        return Headline(TO_REVIEW, Attention.WAITING_ON_YOU, to_review[0], to_review[0].top)
    return None
