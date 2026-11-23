"""Push options: what a push asks Gerrit for, and how it rides on the ``refs/for`` refspec.

Reviewers (under the ``push`` strategy), topic, WIP, private and the drafts decision are sent
as Gerrit magic-ref options (``%r=…,wip,topic=…``). Does not print or prompt.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import quote

from gerrit_workflow_tools.core.reviewer import ReviewerStrategy


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
