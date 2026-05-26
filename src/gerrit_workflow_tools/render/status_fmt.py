"""Plain-text status token formatters — no ANSI, suitable for editable contexts (e.g. rebase todo)."""

from __future__ import annotations

from gerrit_workflow_tools.core.gerrit_change_status import LogCommit


def patchset_token(commit: LogCommit) -> str:
    """Single-character patchset status: p/n/o/m/!/a/-."""
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


def attention_text(commit: LogCommit) -> str:
    """Short plain-text annotation for a trailing ``# …`` column, or empty string."""
    if commit.abandoned:
        return "abandoned"
    if not commit.pushed:
        return "not-pushed"
    if commit.patchset_status == "merged-same":
        return ""
    if commit.patchset_status == "merged-drift":
        return "merged drift"
    if commit.patchset_status == "merged-unknown":
        return "merged (equiv. unknown)"
    parts: list[str] = []
    if commit.ci_failures:
        parts.append(f"CI failed: {commit.ci_failures[0]}")
    elif commit.verified is not None and commit.verified <= -1:
        parts.append("build failed")
    if commit.comments_unresolved > 0:
        n = commit.comments_unresolved
        noun = "comment" if n == 1 else "comments"
        parts.append(f"{n} unresolved {noun}")
    if not parts and commit.submittable:
        return "submittable"
    return ", ".join(parts)
