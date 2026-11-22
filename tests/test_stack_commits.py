"""Tests for upstream-based stack and :class:`~gerrit_workflow_tools.stack.Commit` parsing."""

from __future__ import annotations

from pathlib import Path

from gerrit_workflow_tools.core.stack import (
    commits_in_range,
    merge_base_with_target,
)


def test_commits_in_range_are_commit_objects_with_change_ids(stack_repo: Path) -> None:
    _, _, tip = merge_base_with_target(stack_repo)
    rows = commits_in_range(stack_repo, f"{tip}..HEAD")
    assert len(rows) == 4
    assert all(c.change_id and c.change_id.startswith("I") for c in rows)
    assert rows[0].subject == "Refactor parser init"
    assert rows[-1].subject == "Cleanup after experiment"
