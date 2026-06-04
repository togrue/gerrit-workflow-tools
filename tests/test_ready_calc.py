# Spec: docu/spec/commands/push.md (ready boundary / stop patterns)
# Covers: stop-pattern boundary, --all, merged side branch first-parent vs full DAG, empty range

from __future__ import annotations

from pathlib import Path

from gerrit_workflow_tools.core.config import clear_gerrit_git_config_cache, stop_patterns
from gerrit_workflow_tools.core.git_run import git
from gerrit_workflow_tools.core.ready_calc import compute_ready
from gerrit_workflow_tools.core.stack import commits_in_range, merge_base_with_target
from tests.fixtures import configure_gerrit_target, make_repo_with_merged_side_branch


def _merge_branch_repo(tmp_path: Path) -> Path:
    return make_repo_with_merged_side_branch(tmp_path / "r")


def test_compute_ready_stop_pattern_boundary_push_tip_before_block(stack_repo: Path) -> None:
    """Default stop pattern (^test!) blocks at commit 3; push tip is the prior commit."""
    clear_gerrit_git_config_cache()
    _fork, _display, target_tip = merge_base_with_target(stack_repo)
    rows = commits_in_range(stack_repo, f"{target_tip}..HEAD", first_parent=True)
    assert len(rows) >= 3
    boundary_subject = rows[2].subject
    assert "test!" in boundary_subject.lower()

    result = compute_ready(stack_repo, stop_patterns=[r"^test!"])
    assert result.pushable_count == 2
    assert result.boundary_sha == rows[2].sha
    assert result.push_tip_sha == rows[1].sha
    assert result.push_range == f"{target_tip}..{rows[1].sha}"


def test_compute_ready_all_commits_ignores_stop_pattern(stack_repo: Path) -> None:
    """``all_commits=True`` (--all) includes commits through HEAD despite stop pattern."""
    clear_gerrit_git_config_cache()
    _fork, _display, target_tip = merge_base_with_target(stack_repo)
    rows = commits_in_range(stack_repo, f"{target_tip}..HEAD", first_parent=True)

    result = compute_ready(stack_repo, all_commits=True, stop_patterns=[])
    assert result.pushable_count == len(rows)
    assert result.boundary_sha is None
    assert "ignored (--all)" in result.boundary_reason
    assert result.push_tip_sha == rows[-1].sha


def test_compute_ready_empty_range_above_upstream(stack_repo: Path) -> None:
    """When HEAD equals upstream, result is stable with zero pushable commits."""
    clear_gerrit_git_config_cache()
    git("checkout", "main", cwd=stack_repo)
    git("merge", "feature", cwd=stack_repo)
    git("branch", "--set-upstream-to", "main", "main", cwd=stack_repo, check=False)
    configure_gerrit_target(stack_repo, "main")

    result = compute_ready(stack_repo, stop_patterns=[])
    assert result.pushable_count == 0
    assert result.push_tip_sha is None
    assert result.push_range is None
    assert result.boundary_sha is None


def test_compute_ready_with_merged_side_branch_counts_only_first_parent_commits(
    tmp_path: Path,
) -> None:
    """
    Regression: merging a side branch must not bloat the push commit list.

    The push range should contain only the first-parent commits on the feature
    branch (local work + merge commit = 2), not the side-branch commits (S1,
    S2) that are reachable via the merge commit's second parent.
    """
    repo = _merge_branch_repo(tmp_path)
    result = compute_ready(repo, all_commits=True, stop_patterns=[])
    assert result.pushable_count == 2, (
        f"expected 2 first-parent commits (local work + merge), got {result.pushable_count}"
    )


def test_compute_ready_follow_merges_restores_all_parents_count(tmp_path: Path) -> None:
    """
    ``--follow-merges`` (first_parent=False) must restore the full-DAG count.

    With ``first_parent=False``, ``compute_ready`` traverses both parents of the
    merge commit and returns 4 commits (local + S1 + S2 + merge-M).
    """
    repo = _merge_branch_repo(tmp_path)
    result = compute_ready(repo, all_commits=True, first_parent=False, stop_patterns=[])
    assert result.pushable_count == 4, f"expected 4 commits with full-DAG traversal, got {result.pushable_count}"


def test_compute_ready_zero_pushable_when_first_commit_blocks(stack_repo: Path) -> None:
    """Stop pattern on the first commit above upstream yields nothing pushable."""
    rows = commits_in_range(stack_repo, "main..HEAD", first_parent=True)
    first_subject = rows[0].subject
    git("config", "--unset-all", "gerrit.stopPattern", cwd=stack_repo, check=False)
    git("config", "--add", "gerrit.stopPattern", f"^{first_subject}$", cwd=stack_repo)
    clear_gerrit_git_config_cache()

    result = compute_ready(stack_repo, stop_patterns=stop_patterns(stack_repo))
    assert result.pushable_count == 0
    assert result.push_tip_sha is None
    assert result.push_range is None
    assert result.boundary_sha == rows[0].sha
