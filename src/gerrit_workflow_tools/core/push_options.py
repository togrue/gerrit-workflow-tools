"""Push options: what a push asks Gerrit for, how it rides on the ``refs/for`` refspec, and the
``git push`` runs of a push plan.

Reviewers (under the ``push`` strategy), topic, WIP, private and the drafts decision are sent
as Gerrit magic-ref options (``%r=…,wip,topic=…``). Does not print or prompt.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING
from urllib.parse import quote

from gerrit_workflow_tools.core.reviewer import ReviewerStrategy


if TYPE_CHECKING:
    from gerrit_workflow_tools.core.push import PushPlan
    from gerrit_workflow_tools.core.stack import Commit


@dataclass(frozen=True)
class PushOptions:
    reviewers: tuple[str, ...]
    strategy: ReviewerStrategy
    topic: str | None = None
    wip: bool = False
    ready: bool = False
    """Mark the changes ready for review (``%ready``), the way back from :attr:`wip`."""
    private: bool = False
    remove_private: bool = False
    """Make the changes public again (``%remove-private``), the way back from :attr:`private`."""
    publish_comments: bool | None = None
    """Your drafts on the updated changes: ``True`` publish them, ``False`` keep them (overriding
    an account preference that publishes on push), ``None`` say nothing (there are none)."""
    chain_reviewers: bool = False
    """Give the changes the push creates the people who review the rest of their chain
    (:attr:`core.push.PushPlan.chain_people`), over REST after the push."""


def refspec_option_segments(
    *,
    reviewers: Sequence[str],
    strategy: ReviewerStrategy,
    wip: bool,
    private: bool,
    topic: str | None,
    publish_comments: bool | None = None,
    ready: bool = False,
    remove_private: bool = False,
) -> list[str]:
    parts: list[str] = []
    if strategy == ReviewerStrategy.PUSH:
        parts.extend(f"r={name}" for name in reviewers)
    if wip:
        parts.append("wip")
    if ready:
        parts.append("ready")
    if private:
        parts.append("private")
    if remove_private:
        parts.append("remove-private")
    if topic:
        parts.append(f"topic={quote(topic, safe='')}")
    if publish_comments is not None:
        parts.append("publish-comments" if publish_comments else "no-publish-comments")
    return parts


def push_refspec(tip: str, push_branch: str, options: PushOptions) -> str:
    ref = f"{tip}:refs/for/{push_branch}"
    parts = refspec_option_segments(
        reviewers=options.reviewers,
        strategy=options.strategy,
        wip=options.wip,
        ready=options.ready,
        private=options.private,
        remove_private=options.remove_private,
        topic=options.topic,
        publish_comments=options.publish_comments,
    )
    if parts:
        ref += "%" + ",".join(parts)
    return ref


def push_argv(plan: PushPlan) -> list[str]:
    return ["git", "push", plan.remote, push_refspec(plan.tip_sha, plan.push_branch, plan.options)]


def wip_commits(plan: PushPlan) -> tuple[Commit, ...]:
    """The commits *plan* pushes as work-in-progress: from :attr:`PushPlan.wip_from` up, else all or none."""
    if plan.wip_from is None:
        return plan.commits if plan.options.wip else ()
    shas = [commit.sha for commit in plan.commits]
    return plan.commits[shas.index(plan.wip_from) :]


def push_argvs(plan: PushPlan) -> list[list[str]]:
    """The ``git push`` runs for *plan*: one, or two when only its top is WIP.

    Gerrit applies ``%wip`` to every change a push creates or updates, so a ready lower part
    goes first and the WIP top second (where the lower commits are already Gerrit's, unchanged).
    """
    wip = wip_commits(plan)
    if plan.wip_from is None or not wip:
        return [push_argv(plan)]
    as_wip = replace(plan.options, wip=True, ready=False)
    if len(wip) == len(plan.commits):
        return [["git", "push", plan.remote, push_refspec(plan.tip_sha, plan.push_branch, as_wip)]]
    ready_tip = plan.commits[len(plan.commits) - len(wip) - 1].sha
    as_ready = replace(plan.options, wip=False)
    return [
        ["git", "push", plan.remote, push_refspec(ready_tip, plan.push_branch, as_ready)],
        ["git", "push", plan.remote, push_refspec(plan.tip_sha, plan.push_branch, as_wip)],
    ]
