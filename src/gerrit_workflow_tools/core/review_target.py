"""What a review acts on: the review chain it walks (Gerrit's related changes of the reviewed
patchset) and the changes a vote goes to.

Which patchset a review checks out is :mod:`~gerrit_workflow_tools.core.gerrit_patchset`'s;
:mod:`~gerrit_workflow_tools.core.review_checkout` only runs git.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from gerrit_workflow_tools.core.gerrit.change_view import ChangeView
from gerrit_workflow_tools.core.gerrit.rest import GerritApiError
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.gerrit.stack_context import ChangeResolutionError
from gerrit_workflow_tools.core.gerrit_change_status import CommentChain
from gerrit_workflow_tools.core.review_checkout import chain_numbers
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
