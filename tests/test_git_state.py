from __future__ import annotations

import shutil
from pathlib import Path

from gerrit_workflow_tools.core.config import branch_gerrit_target, clear_gerrit_git_config_cache
from gerrit_workflow_tools.core.git_run import git, git_out
from gerrit_workflow_tools.core.git_state import (
    effective_gerrit_destination_branch,
    head_is_linear_on_remote_gerrit_target,
    infer_nearest_remote_tracking_branch,
    rebase_in_progress_branch,
    resolve_rebase_onto_remote_ref,
    resolve_working_branch,
)
from tests.helpers import write_rebase_head


def test_rebase_in_progress_branch_reads_git_rebase_state(stack_repo: Path) -> None:
    write_rebase_head(stack_repo, "feature", state_dir="rebase-merge")
    assert rebase_in_progress_branch(stack_repo) == "feature"

    git_dir = Path(git_out("rev-parse", "--git-dir", cwd=stack_repo))
    if not git_dir.is_absolute():
        git_dir = stack_repo / git_dir
    shutil.rmtree(git_dir / "rebase-merge")
    write_rebase_head(stack_repo, "apply-branch", state_dir="rebase-apply")
    assert rebase_in_progress_branch(stack_repo) == "apply-branch"


def test_resolve_working_branch_prefers_checked_out_branch(stack_repo: Path) -> None:
    assert resolve_working_branch(stack_repo) == "feature"


def test_resolve_working_branch_prefers_rebase_branch_over_points_at_head(stack_repo: Path) -> None:
    git("branch", "rebasing", "HEAD~1", cwd=stack_repo)
    git("checkout", "--detach", "HEAD", cwd=stack_repo)
    write_rebase_head(stack_repo, "rebasing", state_dir="rebase-merge")

    assert resolve_working_branch(stack_repo) == "rebasing"


def test_resolve_rebase_onto_remote_ref(stack_repo: Path) -> None:
    clear_gerrit_git_config_cache()
    git("update-ref", "refs/remotes/origin/main", "main", cwd=stack_repo)
    assert resolve_rebase_onto_remote_ref(stack_repo) == "origin/main"


def test_head_is_linear_on_remote_gerrit_target(stack_repo: Path) -> None:
    clear_gerrit_git_config_cache()
    m = git_out("rev-parse", "main", cwd=stack_repo)
    git("update-ref", "refs/remotes/origin/main", m, cwd=stack_repo)
    ok, onto = head_is_linear_on_remote_gerrit_target(stack_repo)
    assert ok
    assert onto == "origin/main"


def test_resolve_rebase_onto_remote_ref_from_upstream_without_gerrit_target(tmp_path: Path) -> None:
    """Upstream on origin/main resolves from branch upstream."""
    repo = tmp_path / "r"
    repo.mkdir()
    git("init", "-b", "main", cwd=repo)
    (repo / "f").write_text("x", encoding="utf-8")
    git("add", "f", cwd=repo)
    git("commit", "-m", "init", cwd=repo)
    git("remote", "add", "origin", str(repo.resolve()), cwd=repo)
    git("fetch", "origin", cwd=repo)
    git("checkout", "-b", "topic", cwd=repo)
    git("branch", "--set-upstream-to=origin/main", "topic", cwd=repo)
    m = git_out("rev-parse", "main", cwd=repo)
    git("update-ref", "refs/remotes/origin/main", m, cwd=repo)
    clear_gerrit_git_config_cache()
    assert resolve_rebase_onto_remote_ref(repo) == "origin/main"
    assert effective_gerrit_destination_branch(repo) == "origin/main"


def test_effective_gerrit_destination_branch_prefers_gerrit_target(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    git("init", "-b", "main", cwd=repo)
    (repo / "f").write_text("x", encoding="utf-8")
    git("add", "f", cwd=repo)
    git("commit", "-m", "init", cwd=repo)
    git("remote", "add", "origin", str(repo.resolve()), cwd=repo)
    git("checkout", "-b", "feature", cwd=repo)
    _set_upstream_to_dev(repo)
    git("config", "branch.feature.gerritTarget", "main", cwd=repo)
    clear_gerrit_git_config_cache()

    assert branch_gerrit_target(repo, "feature") == "main"
    assert effective_gerrit_destination_branch(repo, "feature") == "main"
    assert effective_gerrit_destination_branch(repo, "feature") != "origin/dev"


def _set_upstream_to_dev(repo: Path) -> None:
    main_sha = git_out("rev-parse", "main", cwd=repo)
    git("update-ref", "refs/remotes/origin/dev", main_sha, cwd=repo)
    git("branch", "--set-upstream-to=origin/dev", "feature", cwd=repo)


def test_resolve_rebase_onto_remote_ref_from_upstream_origin_slash_branch(tmp_path: Path) -> None:
    """Upstream origin/dev must not become origin/origin/dev."""
    repo = tmp_path / "r"
    repo.mkdir()
    git("init", "-b", "dev2", cwd=repo)
    (repo / "f").write_text("x", encoding="utf-8")
    git("add", "f", cwd=repo)
    git("commit", "-m", "init", cwd=repo)
    git("remote", "add", "origin", str(repo.resolve()), cwd=repo)
    git("update-ref", "refs/remotes/origin/dev", "HEAD", cwd=repo)
    git("config", "branch.dev2.remote", "origin", cwd=repo)
    git("config", "branch.dev2.merge", "refs/heads/dev", cwd=repo)
    clear_gerrit_git_config_cache()
    assert resolve_rebase_onto_remote_ref(repo) == "origin/dev"


def test_infer_nearest_remote_tracking_branch(tmp_path: Path) -> None:
    """Symmetric divergence vs origin/main: one local commit on feature."""
    clear_gerrit_git_config_cache()
    repo = tmp_path / "r"
    repo.mkdir()
    git("init", "-b", "main", cwd=repo)
    (repo / "f").write_text("1\n", encoding="utf-8")
    git("add", "f", cwd=repo)
    git("commit", "-m", "init", cwd=repo)
    main_sha = git_out("rev-parse", "HEAD", cwd=repo)
    git("checkout", "-b", "feature", cwd=repo)
    (repo / "f").write_text("2\n", encoding="utf-8")
    git("commit", "-am", "feat", cwd=repo)
    git("update-ref", "refs/remotes/origin/main", main_sha, cwd=repo)
    got = infer_nearest_remote_tracking_branch(repo)
    assert got is not None
    abbrev, sym, ahead, behind = got
    assert abbrev == "origin/main"
    assert sym == 1 and ahead == 1 and behind == 0


def test_infer_nearest_remote_tracking_branch_only_searches_gerrit_remote(tmp_path: Path) -> None:
    clear_gerrit_git_config_cache()
    repo = tmp_path / "gerrit-only"
    repo.mkdir()
    git("init", "-b", "main", cwd=repo)
    (repo / "f").write_text("1\n", encoding="utf-8")
    git("add", "f", cwd=repo)
    git("commit", "-m", "init", cwd=repo)
    main_sha = git_out("rev-parse", "HEAD", cwd=repo)
    git("checkout", "-b", "feature", cwd=repo)
    (repo / "f").write_text("2\n", encoding="utf-8")
    git("commit", "-am", "feat", cwd=repo)
    feature_sha = git_out("rev-parse", "HEAD", cwd=repo)
    git("config", "gerrit.remote", "gerrit", cwd=repo)
    git("update-ref", "refs/remotes/gerrit/main", main_sha, cwd=repo)
    git("update-ref", "refs/remotes/origin/feature", feature_sha, cwd=repo)

    got = infer_nearest_remote_tracking_branch(repo)

    assert got is not None
    abbrev, sym, ahead, behind = got
    assert abbrev == "gerrit/main"
    assert sym == 1 and ahead == 1 and behind == 0


def test_infer_nearest_remote_tracking_branch_without_remotes(tmp_path: Path) -> None:
    clear_gerrit_git_config_cache()
    repo = tmp_path / "r2"
    repo.mkdir()
    git("init", "-b", "main", cwd=repo)
    (repo / "f").write_text("1\n", encoding="utf-8")
    git("add", "f", cwd=repo)
    git("commit", "-m", "init", cwd=repo)
    assert infer_nearest_remote_tracking_branch(repo) is None
