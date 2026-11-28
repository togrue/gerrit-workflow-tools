"""Push options on changes Gerrit already has at that commit, set over REST. Does not print or prompt.

Gerrit applies magic-ref options (``%wip``, ``%ready``, ``%topic=…``, ``%r=…``) only to the
changes a push gives a new patch set. For a commit that already is its change's current
patch set, a push changes nothing, so ``ger push`` sets the same state through the change's
REST endpoints instead: ``POST …/wip`` / ``…/ready``, ``POST`` / ``DELETE …/private``, ``PUT …/topic`` and
the reviewer batch. Only what differs from the change's current state is sent, so a push
without these options makes no extra call. ``publish-comments`` is the same: your drafts on such
a change are published with a review (``POST …/review``), since the push gives Gerrit nothing to
publish them with.

A commit Gerrit has as an older patch set of its change (**outdated**) gets nothing: neither
a patch set nor options over REST, so Gerrit keeps its newer patch set as it is.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
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
class OutdatedChange:
    """A commit about to be pushed that Gerrit has as an older patch set of its change: it is not sent."""

    commit: Commit
    number: int | None
    patch_set: int | None
    """The local commit's patch-set number."""
    current_patch_set: int | None

    @classmethod
    def of(cls, commit: Commit, view: ChangeView) -> OutdatedChange:
        revision = view.revisions.get(commit.sha.lower())
        return cls(commit, view.number, revision.number if revision else None, view.current_revision_number)

    @property
    def take_it(self) -> str:
        return f"`ger checkout change:{self.number} --update`"

    def describe(self) -> str:
        """``<sha> outdated: Gerrit keeps patchset 2 of change N (…)``, as ``ger log`` names the next step."""
        return (
            f"{self.commit.short_sha} outdated: Gerrit keeps patchset {self.current_patch_set} "
            f"of change {self.number} ({self.take_it})"
        )


@dataclass(frozen=True)
class StateUpdate:
    """What a push sets over REST on one unchanged change. ``None`` / empty leaves that part alone."""

    change: UnchangedChange
    wip: bool | None = None
    private: bool | None = None
    topic: str | None = None
    reviewers: tuple[str, ...] = ()
    drafts: int = 0
    """Your drafts on the change to publish; ``0`` leaves them drafts."""

    def describe(self) -> str:
        """The update in push-option words: ``wip``, ``ready``, ``private``, ``remove-private``, ``topic=x``…"""
        parts = [] if self.wip is None else ["wip" if self.wip else "ready"]
        parts += [] if self.private is None else ["private" if self.private else "remove-private"]
        parts += [f"topic={self.topic}"] if self.topic else []
        parts += [f"r={name}" for name in self.reviewers]
        parts += [f"publish-comments ({self.drafts} draft{'s' if self.drafts > 1 else ''})"] if self.drafts else []
        return ",".join(parts)


@dataclass(frozen=True)
class StateUpdateFailure:
    update: StateUpdate
    error: str


def planned_updates(
    options: PushOptions, unchanged: Sequence[UnchangedChange], drafts: Mapping[str, int] | None = None
) -> tuple[StateUpdate, ...]:
    """The REST updates *options* ask for on *unchanged*: only where a change's state differs.

    *drafts* counts your drafts per short SHA; they are published where *options* publish comments.
    """
    updates: list[StateUpdate] = []
    for change in unchanged:
        wip = True if options.wip and not change.wip else False if options.ready and change.wip else None
        private = True if options.private and not change.private else None
        private = False if options.remove_private and change.private else private
        topic = options.topic if options.topic and options.topic != change.topic else None
        reviewers = _missing_reviewers(options, change)
        published = (drafts or {}).get(change.commit.short_sha, 0) if options.publish_comments else 0
        update = StateUpdate(change, wip=wip, private=private, topic=topic, reviewers=reviewers, drafts=published)
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
    options: PushOptions,
    unchanged: Sequence[UnchangedChange],
    outdated: Sequence[OutdatedChange] = (),
    *,
    rejected: bool,
) -> str | None:
    """Options that set nothing: on outdated changes, or where Gerrit took no new patch set and ger could
    not read the changes."""
    states = (options.wip, options.ready, options.private, options.remove_private)
    asked = bool(options.topic) or any(states)
    if outdated and (asked or options.reviewers):
        return (
            f"warning: push options not applied to {', '.join(c.commit.short_sha for c in outdated)}: "
            f"Gerrit has newer patchsets of these changes ({outdated[0].take_it})"
        )
    pushed_reviewers = bool(options.reviewers) and options.strategy == ReviewerStrategy.PUSH
    if not rejected or unchanged or not (asked or pushed_reviewers):
        return None
    return (
        "warning: push options not applied: Gerrit already has these commits, and without Gerrit's "
        "view of the changes ger cannot set them over REST (check ger.user and ger.token)"
    )


def apply_updates(service: GerritService, updates: Sequence[StateUpdate]) -> list[StateUpdateFailure]:
    """Send *updates*; the cached state of every change touched is dropped, failed or not."""
    failures: list[StateUpdateFailure] = []
    for update in updates:
        triplet = update.change.triplet
        try:
            if update.wip is not None:
                service.rest.set_wip(triplet, update.wip)
            if update.private is not None:
                service.rest.set_private(triplet, update.private)
            if update.topic:
                service.rest.set_topic(triplet, update.topic)
            if update.reviewers:
                service.changes.set_reviewers(triplet, add=list(update.reviewers))
            if update.drafts:
                service.comments.publish_drafts(triplet)
        except GerritApiError as error:
            failures.append(StateUpdateFailure(update, str(error)))
        finally:
            service.cache.invalidate_changes([triplet])
    return failures
