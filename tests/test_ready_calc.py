# Spec: docu/spec/commands/push.md (ready boundary / stop patterns)
# Covers: stop-pattern boundary, --all, merged side branch first-parent vs full DAG, empty range,
#         Change-Id ready-boundary composition

from __future__ import annotations

from pathlib import Path

from gerrit_workflow_tools.core.ready_calc import compute_ready
from gerrit_workflow_tools.core.stack import commits_in_range, merge_base_with_target


def test_compute_ready_stop_pattern_boundary_push_tip_before_block(stack_repo: Path) -> None:
    """Default stop pattern (^test!) blocks at commit 3; push tip is the prior commit."""
    _fork, _display, target_tip = merge_base_with_target(stack_repo)
    rows = commits_in_range(stack_repo, f"{target_tip}..HEAD", first_parent=True)
    assert len(rows) >= 3
    boundary_subject = rows[2].subject
    assert "test!" in boundary_subject.lower()

    result = compute_ready(stack_repo, stop_pattern=r"^test!")
    assert result.pushable_count == 2
    assert result.boundary_sha == rows[2].sha
    assert result.push_tip_sha == rows[1].sha
    assert result.push_range == f"{target_tip}..{rows[1].sha}"


def test_compute_ready_duplicate_change_id_blocks_at_second(dup_repo: Path) -> None:
    """Duplicate Change-Id blocks at the second commit; first remains pushable."""
    rows = commits_in_range(dup_repo, "main..HEAD", first_parent=True)
    assert len(rows) == 2
    result = compute_ready(dup_repo, stop_pattern="")
    assert result.pushable_count == 1
    assert result.push_tip_sha == rows[0].sha
    assert result.boundary_sha == rows[1].sha
    assert "duplicate Change-Id" in result.boundary_reason
