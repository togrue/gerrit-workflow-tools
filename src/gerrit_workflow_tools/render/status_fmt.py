"""Plain-text status token formatters — no ANSI, suitable for editable contexts (e.g. rebase todo)."""

from __future__ import annotations

from gerrit_workflow_tools.core.attention import Attention
from gerrit_workflow_tools.core.gerrit_change_status import LogCommit
from gerrit_workflow_tools.core.inbox import OUTGOING, TO_REVIEW


def patchset_token(commit: LogCommit) -> str:
    """Single-character patchset status: p/n/o/m/!/?/a/-, and ``~`` when Gerrit could not be asked."""
    if commit.abandoned:
        return "a"
    status = commit.patchset_status
    if status == "merged-same":
        return "m"
    if status == "merged-drift":
        return "!"
    if status == "merged-unknown":
        return "?"
    if status == "active":
        return "p"
    if status == "newer":
        return "n"
    if status == "outdated":
        return "o"
    if status == "unknown":
        return "~"
    return "-"


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
    (Attention.NOT_PUSHED, Attention.NOT_PUSHED),
    (Attention.MERGED_DRIFT, "merged drift"),
    (Attention.MERGED_UNKNOWN, "merged (equiv. unknown)"),
)


def unresolved_comments_text(count: int) -> str:
    """``1 unresolved comment`` / ``3 unresolved comments``."""
    noun = "comment" if count == 1 else "comments"
    return f"{count} unresolved {noun}"


SECTION_HEADINGS = {TO_REVIEW: "to review", OUTGOING: "outgoing"}
"""What ``ger inbox`` calls each **inbox section**, in every view."""

# Not attention reasons, but hints all the same: keys of :func:`attention_hints` beside the reasons.
PRIVATE = "private"
SUBMITTABLE = "submittable"

# Awaiting-review is left out: it is true of every commit under review, so `ger log` would
# print it on nearly every line. `ger show` and JSON list it.
_STATE_TEXT = (
    (Attention.AHEAD_OF_GERRIT, "ahead of gerrit"),
    (Attention.DIVERGED, "diverged: Gerrit has a patchset you never had"),
    (Attention.OUTDATED_PATCHSET, "outdated patchset"),
    (Attention.REVIEW_ISSUES, "review issues"),
    (Attention.CHAIN_BLOCKED, "chain blocked"),
)


def attention_hints(commit: LogCommit) -> list[tuple[str, str]]:
    """``ger log``'s trailing hints as (key, text): the key is the attention reason, or :data:`PRIVATE` /
    :data:`SUBMITTABLE`. A reason that leaves nothing to review replaces every other hint.

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
    if Attention.UNRESOLVED_COMMENTS in reasons:
        hints.append((Attention.UNRESOLVED_COMMENTS, unresolved_comments_text(commit.comments_unresolved)))
    if Attention.NO_REVIEWERS in reasons:
        hints.append((Attention.NO_REVIEWERS, "no reviewers"))
    if commit.submittable and not hints:
        hints.append((SUBMITTABLE, "submittable"))
    hints.extend((reason, text) for reason, text in _STATE_TEXT if reason in reasons)
    return hints


def member_hints(reasons: tuple[str, ...], comments: int) -> list[tuple[str, str]]:
    """``ger inbox``'s hints for one **review chain** member as (reason, text), most urgent first."""
    hints: list[tuple[str, str]] = []
    if Attention.CI_FAILED in reasons:
        hints.append((Attention.CI_FAILED, "build failed"))
    if Attention.UNRESOLVED_COMMENTS in reasons:
        text = unresolved_comments_text(comments) if comments else "unresolved comments"
        hints.append((Attention.UNRESOLVED_COMMENTS, text))
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
    if Attention.UNRESOLVED_COMMENTS in reasons:
        parts.append(unresolved_comments_text(commit.comments_unresolved))
    if not parts and commit.submittable:
        return "submittable"
    return ", ".join(parts)
