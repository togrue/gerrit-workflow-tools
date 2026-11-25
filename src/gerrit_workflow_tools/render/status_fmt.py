"""Plain-text status token formatters — no ANSI, suitable for editable contexts (e.g. rebase todo)."""

from __future__ import annotations

from collections.abc import Collection, Sequence

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


def legend_line(commits: Sequence[LogCommit]) -> str:
    """The status tokens *commits* show, in one line (``p pushed · n newer locally · v/cr …``): the
    words of :func:`status_legend`, as short as a commented line in a rebase todo wants them."""
    shown = {patchset_token(commit) for commit in commits}
    tokens = [(token, meaning) for token, meaning in [*PATCHSET_TOKENS.values(), ABANDONED_TOKEN] if token in shown]
    words = [f"{token} {meaning.split(':')[0]}" for token, meaning in dict(tokens).items()]
    if any(commit.pushed for commit in commits):
        (verified, v_words), (review, cr_words), (comments, com_words) = VOTE_LEGEND
        prefix = (verified.split()[-1].rstrip("?"), review.split()[-1].rstrip("?"))  # `v?` → `v`
        names = (v_words.split(" vote")[0], cr_words.split(" vote")[0])
        words.append(f"{'/'.join(prefix)} {'/'.join(names)} vote{v_words.split(' vote')[1]}")
        words.append(f"{comments} {com_words.split(' (')[0]}")
    return " · ".join(words)


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
    """The other side of a change's comment chains: its author on a change you review, else its reviewer."""
    return AUTHOR if {Attention.WAITING_ON_YOU, Attention.WAITING_ON_AUTHOR} & set(reasons) else REVIEWER


def comment_hints(count: int, awaiting: int = 0, drafted: int = 0, *, side: str = REVIEWER) -> list[tuple[str, str]]:
    """*count* unresolved comment chains as (reason, text): *awaiting* of them wait on the other side
    (*side*), *drafted* of them are answered by drafts of yours. The reasons are
    :func:`core.attention.comment_reasons`'s.

    ``3 unresolved comments``; ``1 of 3 comments to answer`` (the others wait on the other side or
    on your push); ``2 drafts to publish``; ``2 comments wait on the reviewer``.
    """
    yours = count - awaiting - drafted
    hints: list[tuple[str, str]] = []
    if yours > 0:
        text = counted(count, "unresolved comment") if yours == count else f"{yours} of {count} comments to answer"
        hints.append((Attention.UNRESOLVED_COMMENTS, text))
    if drafted:
        hints.append((Attention.DRAFTS_TO_PUBLISH, f"{counted(drafted, 'draft')} to publish"))
    if yours <= 0 and awaiting:
        hints.append(
            (
                Attention.AWAITING_REPLY,
                f"{counted(awaiting, 'comment')} {'waits' if awaiting == 1 else 'wait'} on the {side}",
            )
        )
    return hints


def unresolved_comments_text(count: int, awaiting: int = 0, drafted: int = 0, *, side: str = REVIEWER) -> str:
    """:func:`comment_hints` as one text: ``1 of 3 comments to answer, 2 drafts to publish``."""
    return ", ".join(text for _reason, text in comment_hints(count, awaiting, drafted, side=side))


_COMMENT_REASONS = (Attention.UNRESOLVED_COMMENTS, Attention.DRAFTS_TO_PUBLISH, Attention.AWAITING_REPLY)


def commit_comment_hints(commit: LogCommit) -> list[tuple[str, str]]:
    """:func:`comment_hints` of *commit*, whose turn said apart; only those its reasons hold."""
    side = other_side(commit.attention_reasons)
    hints = comment_hints(
        commit.comments_unresolved, commit.comments_awaiting_reply, commit.comments_drafted, side=side
    )
    return [(reason, text) for reason, text in hints if reason in commit.attention_reasons]


def comments_text(commit: LogCommit) -> str:
    """:func:`commit_comment_hints` of *commit* as one text."""
    return ", ".join(text for _reason, text in commit_comment_hints(commit))


def review_issues_text(commit: LogCommit) -> str:
    """*commit*'s negative Code-Review votes, by whom: ``Code-Review -1 from Bob``."""
    against = [
        f"{vote.value:+d} from {vote.by}" for vote in commit.votes if vote.label == "Code-Review" and vote.value < 0
    ]
    if not against:
        return f"Code-Review {commit.code_review:+d}" if commit.code_review else "negative vote"
    return "Code-Review " + ", ".join(against)


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
    if reason == Attention.DRAFTS_TO_PUBLISH:
        return reason, dict(commit_comment_hints(commit)).get(reason, "drafts to publish")
    if reason in _COMMENT_REASONS:
        return reason, comments_text(commit)
    return reason, _BLOCKING_TEXT[reason]


def chain_blocked_text(blocker: LogCommit) -> str:
    """``chain blocked by 1a2b3c4 (awaiting review)``: the line that closes a stack whose chain is blocked.

    Held back only by comments your drafts answer, it waits on your push, which publishes them.
    """
    key, text = blocking_text(blocker)
    if key == Attention.DRAFTS_TO_PUBLISH:
        return f"chain waits on your push: {blocker.short_sha} has {text}"
    return f"chain blocked by {blocker.short_sha} ({text})"


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
    hints.extend(commit_comment_hints(commit))
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
    else:
        turns = comment_hints(comments, member.comments_awaiting_reply, member.comments_drafted, side=side)
        hints.extend((reason, text) for reason, text in turns if reason in reasons)
    if Attention.REVIEW_ISSUES in reasons:
        hints.append((Attention.REVIEW_ISSUES, "negative vote"))
    return hints


def hints_text(commit: LogCommit) -> str:
    """``ger log``'s trailing hints of *commit* (:func:`attention_hints`) as plain text: ``review issues, …``."""
    return ", ".join(text for _key, text in attention_hints(commit))


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
    if reasons.intersection(_COMMENT_REASONS):
        parts.append(comments_text(commit))
    if Attention.REVIEW_ISSUES in reasons:
        parts.append(review_issues_text(commit))
    if not parts and commit.submittable:
        return "submittable"
    return ", ".join(parts)
