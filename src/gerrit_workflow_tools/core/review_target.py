"""What a review acts on: the review chain it walks (Gerrit's related changes of the reviewed
patchset), the changes a vote goes to, and the vote itself: planned, then sent.

Which patchset a review checks out is :mod:`~gerrit_workflow_tools.core.gerrit_patchset`'s;
:mod:`~gerrit_workflow_tools.core.review_checkout` only runs git. ``ger review --vote`` and
``ger serve``'s ``/vote`` both plan with :func:`plan_vote` and send with :func:`send_vote`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from gerrit_workflow_tools.core.gerrit.change_view import ChangeView
from gerrit_workflow_tools.core.gerrit.comments import PUBLISH_GUARD_ENV, publish_blocked
from gerrit_workflow_tools.core.gerrit.rest import GerritApiError
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.gerrit.stack_context import ChangeResolutionError
from gerrit_workflow_tools.core.gerrit_change_status import CommentChain
from gerrit_workflow_tools.core.gerrit_patchset import resolve_change
from gerrit_workflow_tools.core.review_checkout import ReviewState, ReviewUsageError, chain_numbers, find_review
from gerrit_workflow_tools.core.usage import UsageError
from gerrit_workflow_tools.core.votes import votes_ruled_out


def related_chain_numbers(service: GerritService, key: str, patch_set: int | str = "current") -> list[int]:
    """Open change numbers of the review chain *key* sits in, base first; ``[]`` when Gerrit cannot say."""
    try:
        return chain_numbers(service.rest.get_related(key, patch_set))
    except GerritApiError:
        return []


@dataclass(frozen=True)
class VoteTarget:
    """One change a vote goes to: as Gerrit has it now, your drafts on it, the votes it rules out."""

    change: ChangeView
    drafts: tuple[CommentChain, ...]
    """The chains holding a draft of yours: what publishing sends with the vote."""
    ruled_out: tuple[str, ...]
    """Votes Gerrit does not let you give here (``votes_ruled_out``); empty: it takes them all."""

    @property
    def number(self) -> int:
        """The change number."""
        return self.change.number or 0

    @property
    def draft_count(self) -> int:
        """How many drafts of yours a publish sends."""
        return sum(1 for chain in self.drafts for comment in chain.comments if comment.draft)


def changes_by_number(service: GerritService, numbers: Sequence[int]) -> list[ChangeView]:
    """The changes *numbers* name, in that order, as Gerrit has them now: one light query, details where they moved."""
    rows = service.changes.query(" OR ".join(f"change:{number}" for number in numbers), n=len(numbers))
    views = {view.number: view for view in map(ChangeView.from_payload, rows)}
    missing = [str(number) for number in numbers if number not in views]
    if missing:
        raise ChangeResolutionError(f"Gerrit has no change {', '.join(missing)}")
    return [views[number] for number in numbers]


def vote_targets(service: GerritService, changes: Sequence[ChangeView], votes: Mapping[str, int]) -> list[VoteTarget]:
    """*changes* with your drafts on each (one ``has:draft`` query, then chains only where it found
    some) and the *votes* each rules out."""
    numbers = [change.number for change in changes if change.number is not None]
    drafted = {view.number for view in service.comments.drafted_changes(numbers)}
    return [
        VoteTarget(
            change=change,
            drafts=tuple(service.comments.chains_with_drafts(change.key, change_updated=change.freshness))
            if change.number in drafted
            else (),
            ruled_out=tuple(votes_ruled_out(votes, change.permitted_votes)),
        )
        for change in changes
    ]


def vote_changes(
    service: GerritService, cwd: Path, named: Sequence[str], *, chain: bool
) -> tuple[list[ChangeView], ReviewState | None]:
    """The changes to vote on, as Gerrit has them now, and the review whose patchset the vote must match.

    The changes *named* (changeishes), each read once; else the change under review here. With
    *chain*, every open change of the review chain of the one change named, or of the change under
    review.
    """
    if named:
        views = [resolve_change(service, cwd, changeish).view for changeish in named]
        if not chain:
            return views, None
        if len(views) != 1:
            raise UsageError("a chain vote names one change: the chain is the one it sits in")
        numbers = related_chain_numbers(service, views[0].key)
        return (changes_by_number(service, numbers) if len(numbers) > 1 else views), None
    state = find_review(cwd, service.settings)
    if state is None:
        raise ReviewUsageError("no review going on here: name the change with --change")
    numbers = (related_chain_numbers(service, state.key, state.patch_set) or [state.number]) if chain else []
    return changes_by_number(service, numbers or [state.number]), state


def review_moved(changes: Sequence[ChangeView], state: ReviewState | None) -> str | None:
    """Why the vote would miss what you read: Gerrit's patchset of the change under review is newer."""
    current = next((c.current_revision_number for c in changes if state and c.number == state.number), None)
    if state is None or current is None or current == state.patch_set:
        return None
    number = state.number
    return f"change {number} is at patchset {current}, you reviewed {state.patch_set}: run ger review {number} first"


@dataclass(frozen=True)
class VotePlan:
    """What a vote sends: per change your drafts and the votes, the message on each; nothing sent yet."""

    targets: tuple[VoteTarget, ...]
    votes: Mapping[str, int] = field(default_factory=dict)
    """Empty: your drafts (and the message) go out without a vote."""
    message: str | None = None

    @property
    def takes(self) -> list[VoteTarget]:
        """The changes that take every vote: those it is sent to."""
        return [t for t in self.targets if not t.ruled_out]

    @property
    def skipped(self) -> list[VoteTarget]:
        """The changes that rule a vote out: nothing is sent to them."""
        return [t for t in self.targets if t.ruled_out]

    @property
    def draft_count(self) -> int:
        """How many drafts of yours go out, over every change it is sent to."""
        return sum(t.draft_count for t in self.takes)


def plan_vote(
    service: GerritService, changes: Sequence[ChangeView], votes: Mapping[str, int], message: str | None = None
) -> VotePlan:
    """The plan of a vote with *votes* and *message* on *changes*; :class:`UsageError` when no change takes it."""
    plan = VotePlan(tuple(vote_targets(service, changes, votes)), dict(votes), message or None)
    if votes and not plan.takes:
        raise UsageError("; ".join(f"change {t.number} does not take {', '.join(t.ruled_out)}" for t in plan.skipped))
    return plan


def send_vote(
    service: GerritService, plan: VotePlan, *, sent: Callable[[VoteTarget], None] = lambda _target: None
) -> list[VoteTarget]:
    """Publish *plan*: your drafts, its votes and message on each change that takes them; one REST call each.

    *sent* hears of each change once it went out. Refused (:class:`UsageError`) while
    :data:`PUBLISH_GUARD_ENV` is set, and when there is nothing to send.
    """
    if publish_blocked():
        raise UsageError(f"publishing is blocked: {PUBLISH_GUARD_ENV} is set in the environment")
    if not plan.votes and not plan.message and not plan.draft_count:
        raise UsageError("nothing to publish: no drafts of yours, no vote and no message")
    for target in plan.takes:
        service.comments.publish_drafts(target.change.key, message=plan.message, labels=plan.votes)
        sent(target)
    return plan.takes
