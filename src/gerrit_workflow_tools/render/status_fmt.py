"""Plain-text status token formatters — no ANSI, suitable for editable contexts (e.g. rebase todo)."""

from __future__ import annotations

from gerrit_workflow_tools.core.attention import Attention
from gerrit_workflow_tools.core.gerrit_change_status import LogCommit


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
