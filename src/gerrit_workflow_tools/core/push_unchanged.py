"""Push options on changes Gerrit already has at that commit, set over REST. Does not print or prompt.

Gerrit applies magic-ref options (``%wip``, ``%ready``, ``%topic=…``, ``%r=…``) only to the
changes a push gives a new patch set. For a commit that already is its change's current
patch set, a push changes nothing, so ``ger push`` sets the same state through the change's
REST endpoints instead: ``POST …/wip`` / ``…/ready``, ``POST …/private``, ``PUT …/topic`` and
the reviewer batch. Only what differs from the change's current state is sent, so a push
without these options makes no extra call.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from gerrit_workflow_tools.core.gerrit.change_view import ChangeView
from gerrit_workflow_tools.core.gerrit.rest import GerritApiError
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.push_options import PushOptions
from gerrit_workflow_tools.core.reviewer import ReviewerStrategy
from gerrit_workflow_tools.core.stack import Commit


@dataclass(frozen=True)
class UnchangedChange:
    """A commit about to be pushed that already is its change's current patch set."""

    commit: Commit
    triplet: str
    wip: bool
    private: bool
    topic: str | None
    reviewers: tuple[str, ...] | None
    """Account slugs on the change; ``None`` when Gerrit did not say."""

    @classmethod
    def of(cls, commit: Commit, triplet: str, view: ChangeView) -> UnchangedChange:
        reviewers = None if view.reviewers is None else tuple(account.slug for account in view.reviewers)
        return cls(commit, triplet, view.work_in_progress, view.private, view.topic, reviewers)


@dataclass(frozen=True)
class StateUpdate:
    """What a push sets over REST on one unchanged change. ``None`` / empty leaves that part alone."""

    change: UnchangedChange
    wip: bool | None = None
    private: bool = False
    topic: str | None = None
    reviewers: tuple[str, ...] = ()

    def describe(self) -> str:
        """The update in push-option words: ``ready``, ``wip``, ``private``, ``topic=x``, ``r=alice``."""
        parts = [] if self.wip is None else ["wip" if self.wip else "ready"]
        parts += ["private"] if self.private else []
        parts += [f"topic={self.topic}"] if self.topic else []
        parts += [f"r={name}" for name in self.reviewers]
        return ",".join(parts)


@dataclass(frozen=True)
class StateUpdateFailure:
    update: StateUpdate
    error: str


def planned_updates(options: PushOptions, unchanged: Sequence[UnchangedChange]) -> tuple[StateUpdate, ...]:
    """The REST updates *options* ask for on *unchanged*: only where a change's state differs."""
    updates: list[StateUpdate] = []
    for change in unchanged:
        wip = True if options.wip and not change.wip else False if options.ready and change.wip else None
        private = options.private and not change.private
        topic = options.topic if options.topic and options.topic != change.topic else None
        reviewers = _missing_reviewers(options, change)
        update = StateUpdate(change, wip=wip, private=private, topic=topic, reviewers=reviewers)
        if update.describe():
            updates.append(update)
    return tuple(updates)


def _missing_reviewers(options: PushOptions, change: UnchangedChange) -> tuple[str, ...]:
    """Reviewers ``%r=`` would have added: lazy and overwrite reach every change over REST already."""
    if options.strategy != ReviewerStrategy.PUSH:
        return ()
    present = {slug.casefold() for slug in change.reviewers or ()}
    return tuple(name for name in options.reviewers if name.casefold() not in present)


def unreached_options_warning(
    options: PushOptions, unchanged: Sequence[UnchangedChange], *, rejected: bool
) -> str | None:
    """Gerrit took no new patch set and ger could not read the changes, so options set nothing."""
    pushed_reviewers = bool(options.reviewers) and options.strategy == ReviewerStrategy.PUSH
    asked = bool(options.topic) or options.wip or options.ready or options.private or pushed_reviewers
    if not rejected or unchanged or not asked:
        return None
    return (
        "warning: push options not applied: Gerrit already has these commits, and without Gerrit's "
        "view of the changes ger cannot set them over REST (check gerrit.user and gerrit.token)"
    )


def apply_updates(service: GerritService, updates: Sequence[StateUpdate]) -> list[StateUpdateFailure]:
    """Send *updates*; the cached state of every change touched is dropped, failed or not."""
    failures: list[StateUpdateFailure] = []
    for update in updates:
        triplet = update.change.triplet
        try:
            if update.wip is not None:
                service.rest.set_wip(triplet, update.wip)
            if update.private:
                service.rest.set_private(triplet, True)
            if update.topic:
                service.rest.set_topic(triplet, update.topic)
            if update.reviewers:
                service.changes.set_reviewers(triplet, add=list(update.reviewers))
        except GerritApiError as error:
            failures.append(StateUpdateFailure(update, str(error)))
        finally:
            service.cache.invalidate_changes([triplet])
    return failures
