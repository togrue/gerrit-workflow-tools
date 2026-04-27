"""Tests for upstream-based stack and :class:`~gerrit_workflow_tools.stack.Commit` parsing."""

from __future__ import annotations

from pathlib import Path

from gerrit_workflow_tools.core.stack import (
    commits_in_range,
    merge_base_with_target,
    upstream_tracking_tip_and_display,
)


def test_upstream_tip_aligns_with_merge_base_target_sha(stack_repo: Path) -> None:
    """Local stack base is @{upstream}; merge_base_with_target's third value is that same tip."""
    tip_u, disp_u = upstream_tracking_tip_and_display(stack_repo)
    _fork, disp_m, tip_m = merge_base_with_target(stack_repo)
    assert tip_u == tip_m
    assert disp_u == disp_m


def test_commits_in_range_are_commit_objects_with_change_ids(stack_repo: Path) -> None:
    _, _, tip = merge_base_with_target(stack_repo)
    rows = commits_in_range(stack_repo, f"{tip}..HEAD")
    assert len(rows) == 4
    assert all(c.change_id and c.change_id.startswith("I") for c in rows)
    assert rows[0].subject == "Refactor parser init"
    assert rows[-1].subject == "Cleanup after experiment"
