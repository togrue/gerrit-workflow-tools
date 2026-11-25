"""The inbox: its **inbox sections**, their queries, what a section adds up to, and the **headline**.

Host-scoped (ADR-0004): nothing here resolves a stack context or looks at local commits. Run
inside a clone, the sections are narrowed to that clone's Gerrit project (:func:`project_scope`).
A rebase underway in the clone leads the headline; the caller hands it in
(:class:`RebaseHeadline`), so nothing here reads git.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any

from gerrit_workflow_tools.core.attention import Attention, Severity, attention_severity
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.change_view import ChangeView
from gerrit_workflow_tools.core.gerrit.rest import norm_change_id, parallel_map
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.gerrit_change_status import LogCommit
from gerrit_workflow_tools.core.gerrit_project_id import resolve_gerrit_project_name
from gerrit_workflow_tools.core.rebase_state import RebaseStop
from gerrit_workflow_tools.core.review_chain import ChainMember, ReviewChain, your_turn_since


DEFAULT_TO_REVIEW_QUERY = "is:open -is:wip -is:private -owner:self reviewer:self"
DEFAULT_TO_REVIEW_ALL_QUERY = "is:open -owner:self reviewer:self"
DEFAULT_OUTGOING_QUERY = "is:open owner:self"

TO_REVIEW = "to-review"
OUTGOING = "outgoing"
LOCAL = "local"
"""Not an inbox section: where a :class:`RebaseHeadline` comes from, the checkout."""
REBASE = "rebase"
"""The reason of a :class:`RebaseHeadline`."""

# What the author has to act on, most urgent first.
_HEADLINE_ORDER = (Attention.CI_FAILED, Attention.UNRESOLVED_COMMENTS, Attention.REVIEW_ISSUES)


@dataclass(frozen=True)
class InboxSection:
    """One **inbox section**: a Gerrit query and the review chains assembled from it."""

    name: str
    query: str
    chains: list[ReviewChain]
    not_ready: int | None = None
    """*To review* chains its query leaves out as not ready (WIP, private, no ``Verified``), that
    ``--unready`` shows; ``None`` when not counted."""
    drafted: frozenset[int] = frozenset()
    """The section's changes that hold drafts of yours (asked with ``with_drafts``)."""

    @property
    def waiting_on_you(self) -> list[ReviewChain]:
        """The chains that want you: under *to review* those where it is your turn, every other
        section's all. What the summary, the count and the next step speak of."""
        return [chain for chain in self.chains if chain.your_turn] if self.name == TO_REVIEW else list(self.chains)

    @property
    def waiting_on_author(self) -> list[ReviewChain]:
        """The *to review* chains you have reviewed that wait on their author; none in another section."""
        return [chain for chain in self.chains if not chain.your_turn] if self.name == TO_REVIEW else []


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


@dataclass(frozen=True)
class RebaseHeadline:
    """The **headline** while a rebase is underway in the checkout: finishing it comes before any
    review, so it leads whatever the inbox sections hold."""

    stop: RebaseStop | None
    """Why it is stopped; ``None`` when core cannot tell."""
    commit: LogCommit | None
    """The commit it stopped at, as the **rebasing stack** shows it; ``None`` between commits. Stopped
    folding a fixup into a commit, that commit (:attr:`folding`)."""
    folding: bool = False
    """Stopped on a fixup or squash that folds into :attr:`commit`."""

    section = LOCAL
    reason = REBASE

    @property
    def severity(self) -> Severity:
        """A conflict cannot go on as it is; any other stop only waits for you."""
        return Severity.ERROR if self.stop is RebaseStop.CONFLICT else Severity.WARNING


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
    built-in queries (``reviewer:self``), not of a custom ``ger.inboxToReviewQuery``, which may
    select changes any other way.
    """
    return not settings.inbox_to_review_query


def review_turns(service: GerritService, numbers: Collection[int]) -> dict[int, bool]:
    """Of the changes *numbers*, those you review, each with whether it is your turn
    (:func:`your_turn_since`) or its author's. You review the ones the *to review* section's own
    query (:func:`build_to_review_query`) matches, in any project, with no limit and without
    ``--unready``. One query; none for no *numbers*.

    The Change view asks it, so that a change listed under *to review* says the same there. The list
    is narrower (the clone's project, ``ger.inboxLimit``): a change of another project, or beyond the
    limit, waits on you without being listed.
    """
    if not numbers:
        return {}
    query = build_to_review_query(service.settings, projects=[], include_unready=False)
    changes = " OR ".join(f"change:{number}" for number in sorted(set(numbers)))
    found = [ChangeView.from_payload(row) for row in service.changes.query(f"({query}) ({changes})")]
    me = service.self_account_id()
    turns = {view.number: your_turn_since(view, me) is not None for view in found}
    return {number: turns[number] for number in numbers if number in turns}


def not_ready_why(member: ChainMember) -> str:
    """Why the *to review* query left out *member* of a chain it lists, as far as the change's
    facts tell: ``WIP``, ``private``, ``not verified yet``; else ``not asked of you`` (you are no
    reviewer of it)."""
    if member.work_in_progress:
        return "WIP"
    if member.private:
        return "private"
    if member.verified is None or member.verified < 1:
        return "not verified yet"
    return "not asked of you"


def project_scope(
    cwd: Path | str | None,
    settings: Settings,
    *,
    projects: Sequence[str] = (),
    all_projects: bool = False,
) -> list[str]:
    """The Gerrit projects the inbox is narrowed to; empty means every project on the host.

    Projects named explicitly win. Otherwise ``all_projects`` asks for the whole host; inside a
    clone it is the clone's own project; outside one, ``ger.inboxProjects``.
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


def section_summary(section: InboxSection) -> dict[str, int]:
    """Counts over the chains of *section* that want you (:attr:`InboxSection.waiting_on_you`):
    sizes, oldest clocks, chains with failed CI or comments. *To review* also counts the chains
    that wait on their author."""
    chains = section.waiting_on_you
    summary = {
        "chains": len(chains),
        "changes": sum(chain.depth for chain in chains),
        "oldest_unreviewed_seconds": max((chain.unreviewed_age_seconds for chain in chains), default=0),
        "oldest_wait_seconds": max((chain.wait_age_seconds for chain in chains), default=0),
        "ci_failures": sum(1 for chain in chains if Attention.CI_FAILED in chain.attention_reasons),
        "comments": sum(1 for chain in chains if Attention.UNRESOLVED_COMMENTS in chain.attention_reasons),
    }
    return {**summary, "waiting_on_author": len(section.waiting_on_author)} if section.name == TO_REVIEW else summary


def _touches(chain: ReviewChain, change_ids: Collection[str]) -> bool:
    """Whether a member of *chain* carries one of *change_ids*."""
    return any(norm_change_id(member.change_id) in change_ids for member in chain.members)


def _urgency(chain: ReviewChain) -> int:
    """Position of the chain's most urgent reason in the headline order; then a WIP chain (yours
    to mark ready); past the end when it has neither."""
    ranks = [i for i, reason in enumerate(_HEADLINE_ORDER) if reason in chain.attention_reasons]
    wip = [len(_HEADLINE_ORDER)] if chain.work_in_progress else []
    return min(ranks + wip, default=len(_HEADLINE_ORDER) + 1)


def needs_author(chain: ReviewChain) -> bool:
    """Whether an outgoing chain waits on its author: failed CI, unresolved comments, a negative
    vote, or WIP. Any other waits on reviewers or CI."""
    return _urgency(chain) <= len(_HEADLINE_ORDER)


def load_inbox(
    service: GerritService,
    *,
    now: datetime | None = None,
    projects: Sequence[str] = (),
    all_projects: bool = False,
    include_unready: bool = False,
    limit: int | None = None,
    sections: Sequence[str] = (TO_REVIEW, OUTGOING),
    with_drafts: bool = False,
) -> list[InboxSection]:
    """The wanted inbox *sections*, queried in parallel: *to review*, then *outgoing* (your open
    chains). A section not wanted is not asked for.

    Readiness is a chain's: *to review* lists a chain when its query matches a member, and then
    the whole chain, its members that are not ready among them (:attr:`ChainMember.matched`
    false). Its built-in query is asked beside the same query without the readiness terms, which
    finds them; the chains of that one alone are counted as not ready
    (:attr:`InboxSection.not_ready`). Not with ``--unready`` or a custom query, which leave
    nothing out. Which changes hold drafts of yours is asked once (one light ``has:draft`` query)
    where a comment chain may be answered by one (:attr:`ChainMember.comments_drafted`), and
    with *with_drafts*, beside the sections, for :attr:`InboxSection.drafted`.

    Both are narrowed to :func:`project_scope` of the service's directory. *To review* keeps the
    inbox order (longest unreviewed first) and honours *limit*, else ``ger.inboxLimit``.
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
    gated = TO_REVIEW in queries and not include_unready and not settings.inbox_to_review_query
    context = {TO_REVIEW: build_to_review_query(settings, projects=names, include_unready=True)} if gated else {}
    account = service.self_account_id()
    # Which changes hold drafts of yours: asked once for both sections, when first wanted.
    drafts = service.comments.drafted_lazily(None, n=100)
    fetch = partial(service.fetch_review_chains, now=moment, self_account_id=account, drafted=drafts)
    jobs: list[Callable[[], Any]] = [
        partial(fetch, query, context_query=context.get(name)) for name, query in queries.items()
    ]
    if with_drafts:  # beside the sections, not after them: no wait added
        jobs.append(drafts.numbers)
    answers = parallel_map(jobs)
    drafted = answers.pop() if with_drafts else frozenset()
    fetched: dict[str, list[ReviewChain]] = dict(zip(queries, answers, strict=True))
    not_ready = None
    if TO_REVIEW in fetched:
        not_ready = sum(1 for chain in fetched[TO_REVIEW] if not chain.matched) if gated else None
        fetched[TO_REVIEW] = [chain for chain in fetched[TO_REVIEW] if chain.matched]
    cap = limit if limit is not None else settings.inbox_limit
    if cap is not None and TO_REVIEW in fetched:
        fetched[TO_REVIEW] = fetched[TO_REVIEW][: max(0, cap)]
    if OUTGOING in fetched:
        fetched[OUTGOING].sort(key=lambda chain: (_urgency(chain), chain.wait_age_seconds, chain.top.number))
    return [
        InboxSection(
            name,
            queries[name],
            chains,
            not_ready if name == TO_REVIEW else None,
            frozenset(m.number for chain in chains for m in chain.members if m.number in drafted),
        )
        for name, chains in fetched.items()
    ]


def pick_headline(
    sections: Sequence[InboxSection],
    *,
    current: Collection[str] | None = None,
    rebase: RebaseHeadline | None = None,
) -> Headline | RebaseHeadline | None:
    """The headline: a *rebase* underway in the checkout; else failed CI on an outgoing chain;
    else whichever waited longer of unresolved comments on an outgoing chain (since its last
    activity) and the *to review* chain that waits on your turn longest; else a negative vote on
    an outgoing chain. On an outgoing chain, its base-most such change; on a chain to review, the
    base-most change whose turn is yours.

    *current* is the Change-Ids of the chain you work on (normalised, as
    :func:`norm_change_id` gives them). Given, failed CI is a headline only on a chain with one
    of them: another chain's failure waits until you look at it.
    """
    if rebase is not None:
        return rebase
    by_name = {section.name: section for section in sections}
    outgoing = by_name[OUTGOING].chains if OUTGOING in by_name else []
    # (rank in the headline order, how long it waited, negated): the least wins; a tie keeps the first.
    candidates: list[tuple[int, int, Headline]] = []
    for rank, reason in enumerate(_HEADLINE_ORDER):
        found = _first_with(outgoing, reason, current)
        if found is not None:
            waited = found[0].wait_age_seconds if reason == Attention.UNRESOLVED_COMMENTS else 0
            candidates.append((rank, -waited, Headline(OUTGOING, reason, *found)))
    yours = by_name[TO_REVIEW].waiting_on_you if TO_REVIEW in by_name else []
    if yours:
        chain = yours[0]
        member = next((m for m in chain.members if m.your_turn), chain.top)
        rank = _HEADLINE_ORDER.index(Attention.UNRESOLVED_COMMENTS)
        candidates.append(
            (rank, -chain.unreviewed_age_seconds, Headline(TO_REVIEW, Attention.WAITING_ON_YOU, chain, member))
        )
    return min(candidates, key=lambda candidate: candidate[:2])[2] if candidates else None


def _first_with(
    chains: Sequence[ReviewChain], reason: str, current: Collection[str] | None
) -> tuple[ReviewChain, ChainMember] | None:
    """The first of *chains* with a member that has *reason*, and that member (failed CI only on
    the *current* chain, when it is given)."""
    for chain in chains:
        if reason == Attention.CI_FAILED and current is not None and not _touches(chain, current):
            continue
        for member in chain.members:
            if reason in member.attention_reasons:
                return chain, member
    return None
