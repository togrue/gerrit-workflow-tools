"""Plain-text status token formatters — no ANSI, suitable for editable contexts (e.g. rebase todo)."""

from __future__ import annotations

from collections.abc import Collection

from gerrit_workflow_tools.core.attention import Attention, blocking_reason
from gerrit_workflow_tools.core.gerrit_change_status import LogCommit, PatchsetStatus
from gerrit_workflow_tools.core.inbox import OUTGOING, TO_REVIEW
from gerrit_workflow_tools.core.review_chain import ChainMember
from gerrit_workflow_tools.core.words import counted


# The one definition of each status token and what it means: the columns use it, and so does the
# legend in ``ger log --help`` (:func:`status_legend`), so the two cannot drift apart.
ABANDONED_TOKEN = ("a", "abandoned on Gerrit")
PATCHSET_TOKENS: dict[PatchsetStatus, tuple[str, str]] = {
    PatchsetStatus.ACTIVE: ("p", "pushed: Gerrit's current patchset"),
    PatchsetStatus.NEWER: ("n", "newer locally: to upload"),
    PatchsetStatus.OUTDATED: ("o", "outdated: Gerrit has a newer patchset"),
    PatchsetStatus.ABSENT: ("-", "not on Gerrit"),
    PatchsetStatus.MERGED_SAME: ("m", "merged"),
    PatchsetStatus.MERGED_DRIFT: ("!", "merged, but your commit differs"),
    PatchsetStatus.MERGED_UNKNOWN: ("?", "merged, sameness unknown"),
    PatchsetStatus.UNKNOWN: ("~", "Gerrit could not be asked"),
}
VOTE_LEGEND = (
    ("v+1 v-1 v?", "Verified vote (? none yet)"),
    ("cr+2 … cr-2 cr?", "Code-Review vote (? none yet)"),
    ("com", "unresolved comments (dim: all wait on the other side)"),
)


def patchset_token(commit: LogCommit) -> str:
    """Single-character patchset status (:data:`PATCHSET_TOKENS`, :data:`ABANDONED_TOKEN`)."""
    if commit.abandoned:
        return ABANDONED_TOKEN[0]
    return PATCHSET_TOKENS.get(PatchsetStatus(commit.patchset_status), ("-", ""))[0]


def patchset_words(commit: LogCommit) -> str:
    """What the patchset column says of *commit*, in words (``pushed: Gerrit's current patchset``)."""
    if commit.abandoned:
        return ABANDONED_TOKEN[1]
    return PATCHSET_TOKENS.get(PatchsetStatus(commit.patchset_status), ("-", "not on Gerrit"))[1]


def status_legend() -> str:
    """What each status column token means, for a command's ``--help`` epilog."""
    lines = ["status columns:"]
    for group in ([*PATCHSET_TOKENS.values(), ABANDONED_TOKEN], VOTE_LEGEND):
        width = max(len(token) for token, _meaning in group)
        lines += [f"  {token.ljust(width)}  {meaning}" for token, meaning in group]
    return "\n".join(lines)


def verified_token(v: int | None) -> str:
    """Three-char verified label: ``v+1``, ``v-1``, ``v0 ``, ``v? ``."""
    if v is None:
        return "v? "
    if v >= 1:
        return "v+1"
    if v <= -1:
        return "v-1"
    return "v0 "


def code_review_token(cr: int | None) -> str:
    """Four-char code-review label: ``cr+2``, ``cr+1``, ``cr0 ``, ``cr-1``, ``cr-2``, ``cr? ``."""
    if cr is None:
        return "cr? "
    if cr >= 2:
        return "cr+2"
    if cr == 1:
        return "cr+1"
    if cr == -1:
        return "cr-1"
    if cr <= -2:
        return "cr-2"
    return "cr0 "


def comments_token(count: int) -> str:
    """Three-char comments indicator: ``com`` or blank."""
    return "com" if count > 0 else "   "


GERRIT_UNKNOWN_TEXT = "Gerrit state unknown"
UNSQUASHED_FIXUP_TEXT = "unsquashed fixup commit"

_TERMINAL_TEXT = (
    (Attention.MISSING_CHANGE_ID, "missing Change-Id"),
    (Attention.INVALID_CHANGE_ID, "invalid Change-Id"),
    (Attention.UNSQUASHED_FIXUP, UNSQUASHED_FIXUP_TEXT),
    (Attention.GERRIT_UNKNOWN, GERRIT_UNKNOWN_TEXT),
    (Attention.ABANDONED, "abandoned"),
    (Attention.NOT_PUSHED, "not pushed"),
    (Attention.MERGED_DRIFT, "merged drift"),
    (Attention.MERGED_UNKNOWN, "merged (equiv. unknown)"),
)


REVIEWER, AUTHOR = "reviewer", "author"
"""Who the other side of a comment chain is: the reviewer of your change, the author of one you review."""


def other_side(reasons: Collection[str]) -> str:
    """The other side of a change's comment chains: its author when it waits on your review, else its reviewer."""
    return AUTHOR if Attention.WAITING_ON_YOU in reasons else REVIEWER


def unresolved_comments_text(count: int, awaiting: int = 0, *, side: str = REVIEWER) -> str:
    """*count* unresolved comment chains, *awaiting* of them waiting on the other side (*side*).

    ``3 unresolved comments``; ``1 of 3 comments to answer`` (the others wait on the other side);
    ``2 comments wait on the reviewer``.
    """
    yours = count - awaiting
    if not awaiting:
        return counted(count, "unresolved comment")
    waiting = f"{'waits' if awaiting == 1 else 'wait'} on the {side}"
    if not yours:
        return f"{counted(awaiting, 'comment')} {waiting}"
    return f"{yours} of {count} comments to answer"


def comments_text(commit: LogCommit) -> str:
    """:func:`unresolved_comments_text` of *commit*, whose turn said apart."""
    side = other_side(commit.attention_reasons)
    return unresolved_comments_text(commit.comments_unresolved, commit.comments_awaiting_reply, side=side)


SECTION_HEADINGS = {TO_REVIEW: "to review", OUTGOING: "outgoing"}
"""What ``ger inbox`` calls each **inbox section**, in every view."""

# Not attention reasons, but hints all the same: keys of :func:`attention_hints` beside the reasons.
PRIVATE = "private"
SUBMITTABLE = "submittable"
MERGED = "merged"
NOT_SUBMITTABLE = "not-submittable"
"""The chain blocker's hint when none of its reasons says why Gerrit will not submit it."""

# Awaiting-review is left out: it is true of every commit under review, so `ger log` would
# print it on nearly every line; only the commit that blocks the chain says it. Chain-blocked is
# left out too: the commit that blocks says why, once (:func:`chain_blocked_text`). `ger show`
# and JSON list both.
_STATE_TEXT = (
    (Attention.AHEAD_OF_GERRIT, "ahead of gerrit"),
    (Attention.DIVERGED, "diverged from Gerrit"),
    (Attention.OUTDATED_PATCHSET, "outdated patchset"),
    (Attention.REVIEW_ISSUES, "review issues"),
)

_BLOCKING_TEXT = {
    Attention.MERGED_DRIFT: "merged drift",
    Attention.MERGED_UNKNOWN: "merged (equiv. unknown)",
    Attention.CI_FAILED: "build failed",
    Attention.REVIEW_ISSUES: "review issues",
    Attention.WIP: "WIP",
    Attention.NO_REVIEWERS: "no reviewers",
    Attention.AWAITING_REVIEW: "awaiting review",
}


def blocking_text(commit: LogCommit) -> tuple[str, str]:
    """Why the chain blocker holds the chain back, as (key, text): :func:`core.attention.blocking_reason` in words."""
    reason = blocking_reason(commit)
    if reason is None:
        return NOT_SUBMITTABLE, "not submittable"
    if reason in (Attention.UNRESOLVED_COMMENTS, Attention.AWAITING_REPLY):
        return reason, comments_text(commit)
    return reason, _BLOCKING_TEXT[reason]


def chain_blocked_text(blocker: LogCommit) -> str:
    """``chain blocked by 1a2b3c4 (awaiting review)``: the line that closes a stack whose chain is blocked."""
    return f"chain blocked by {blocker.short_sha} ({blocking_text(blocker)[1]})"


def attention_hints(commit: LogCommit) -> list[tuple[str, str]]:
    """``ger log``'s trailing hints as (key, text): the key is the attention reason, or :data:`PRIVATE`,
    :data:`SUBMITTABLE`, :data:`MERGED` or :data:`NOT_SUBMITTABLE`. A reason that leaves nothing to
    review replaces every other hint. The commit that blocks the chain says why (:func:`blocking_text`).

    Maps ``commit.attention_reasons`` to text; the verdict itself is ``core.attention``'s.
    """
    reasons = set(commit.attention_reasons)
    for reason, text in _TERMINAL_TEXT:
        if reason in reasons:
            return [(reason, text)]
    hints: list[tuple[str, str]] = [(Attention.WIP, "WIP")] if Attention.WIP in reasons else []
    if commit.private:
        hints.append((PRIVATE, "private"))
    if Attention.CI_FAILED in reasons:
        hints.append((Attention.CI_FAILED, "build failed"))
    for reason in (Attention.UNRESOLVED_COMMENTS, Attention.AWAITING_REPLY):
        if reason in reasons:
            hints.append((reason, comments_text(commit)))
    if Attention.NO_REVIEWERS in reasons:
        hints.append((Attention.NO_REVIEWERS, "no reviewers"))
    if commit.submittable and not hints:
        hints.append((SUBMITTABLE, "submittable"))
    hints.extend((reason, text) for reason, text in _STATE_TEXT if reason in reasons)
    if commit.blocks_chain and (block := blocking_text(commit))[0] not in {key for key, _text in hints}:
        hints.append(block)
    if commit.patchset_status == PatchsetStatus.MERGED_SAME:
        hints.insert(0, (MERGED, "merged"))
    return hints


def member_hints(member: ChainMember, *, side: str = REVIEWER) -> list[tuple[str, str]]:
    """``ger inbox``'s hints for one **review chain** member as (reason, text), most urgent first.

    *side* is who the other side of its comment chains is: the reviewer of yours, the author of one you review.
    """
    reasons, comments = member.attention_reasons, member.comments_unresolved
    hints: list[tuple[str, str]] = []
    if Attention.CI_FAILED in reasons:
        hints.append((Attention.CI_FAILED, "build failed"))
    if Attention.UNRESOLVED_COMMENTS in reasons and not comments:
        hints.append((Attention.UNRESOLVED_COMMENTS, "unresolved comments"))
    elif Attention.UNRESOLVED_COMMENTS in reasons or Attention.AWAITING_REPLY in reasons:
        reason = Attention.UNRESOLVED_COMMENTS if Attention.UNRESOLVED_COMMENTS in reasons else Attention.AWAITING_REPLY
        hints.append((reason, unresolved_comments_text(comments, member.comments_awaiting_reply, side=side)))
    if Attention.REVIEW_ISSUES in reasons:
        hints.append((Attention.REVIEW_ISSUES, "negative vote"))
    return hints


def attention_text(commit: LogCommit) -> str:
    """Short plain-text annotation for a trailing ``# …`` column, or empty string.

    Maps ``commit.attention_reasons`` to text; the verdict itself is ``core.attention``'s.
    """
    reasons = set(commit.attention_reasons)
    for reason, text in _TERMINAL_TEXT:
        if reason in reasons:
            return text
    parts: list[str] = ["WIP"] if Attention.WIP in reasons else []
    if commit.private:
        parts.append("private")
    if Attention.CI_FAILED in reasons:
        parts.append(f"CI failed: {commit.ci_failures[0]}" if commit.ci_failures else "build failed")
    if Attention.UNRESOLVED_COMMENTS in reasons or Attention.AWAITING_REPLY in reasons:
        parts.append(comments_text(commit))
    if not parts and commit.submittable:
        return "submittable"
    return ", ".join(parts)
