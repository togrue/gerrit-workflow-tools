"""Change-Id narrowing (spec §3.1): which of one Change-Id's changes a stack means.

Pure: it takes the ChangeInfo rows already fetched or cached and never contacts Gerrit, so
``ger show``, ``ger resolve`` and the multi-branch notes of ``ger log`` narrow alike.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from gerrit_workflow_tools.core.gerrit.resolution import (
    ChangeAmbiguousError,
    Resolution,
    SelectedChange,
    SelectedReason,
)


_INACTIVE_STATUSES = frozenset({"ABANDONED", "MERGED"})


def _is_active_status(status: str) -> bool:
    return status.upper() not in _INACTIVE_STATUSES


def narrow_matches(
    resolution: Resolution,
    rows: list[dict[str, Any]],
    *,
    push_branch: str,
    explicit_target: bool,
) -> Resolution:
    """*resolution* with spec §3.1 narrowing applied to one Change-Id's rows. Never contacts Gerrit."""
    changes = [SelectedChange.from_change_row(row) for row in rows]
    on_branch = [change for change in changes if change.branch == push_branch]
    elsewhere = [change for change in changes if change.branch != push_branch]

    def _picked(selected: SelectedChange, reason: SelectedReason, alternatives: list[SelectedChange]) -> Resolution:
        return replace(
            resolution,
            selected=selected,
            selected_reason=reason,
            ambiguous=reason != "unique",
            alternatives=alternatives,
        )

    if len(changes) == 1 and on_branch:
        return _picked(changes[0], "unique", [])
    if len(on_branch) == 1:
        return _picked(on_branch[0], "target-branch", elsewhere)
    if on_branch:
        active = [change for change in on_branch if _is_active_status(change.status)]
        if len(active) == 1:
            inactive = [change for change in on_branch if change is not active[0]]
            return _picked(active[0], "prefer-open", elsewhere + inactive)
        matched = f"{len(active)} open changes" if active else f"{len(on_branch)} changes"
        raise ChangeAmbiguousError(
            f"ambiguous Change-Id: {matched} on branch {push_branch!r}",
            alternatives=active or on_branch,
        )
    if not explicit_target or not changes:
        return replace(resolution, ambiguous=len(changes) > 1, alternatives=changes)
    if len(changes) == 1:
        return _picked(changes[0], "branch-mismatch", [])
    raise ChangeAmbiguousError(
        f"ambiguous Change-Id: matches changes on other branches (not {push_branch!r})",
        alternatives=changes,
    )


def resolution_from_change_rows(
    change_id: str,
    rows: list[dict[str, Any]],
    *,
    push_branch: str,
    explicit_target: bool = False,
) -> Resolution:
    """Build a :class:`Resolution` from already-fetched/cached ChangeInfo rows."""
    return narrow_matches(
        Resolution(input=change_id, kind="change-id"), rows, push_branch=push_branch, explicit_target=explicit_target
    )
