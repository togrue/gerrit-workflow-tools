from __future__ import annotations

import shutil
from pathlib import Path

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.git_run import git, git_out
from gerrit_workflow_tools.core.git_state import (
    Worktree,
    effective_gerrit_destination_branch,
    head_is_linear_on_remote_gerrit_target,
    resolve_rebase_onto_remote_ref,
    resolve_working_branch,
)
from tests.helpers import write_rebase_head


def test_the_rebasing_branch_is_read_from_git_rebase_state(stack_repo: Path) -> None:
    write_rebase_head(stack_repo, "feature", state_dir="rebase-merge")
    assert Worktree.from_cwd(stack_repo).rebasing_branch() == "feature"

    git_dir = Path(git_out("rev-parse", "--git-dir", cwd=stack_repo))
    if not git_dir.is_absolute():
        git_dir = stack_repo / git_dir
    shutil.rmtree(git_dir / "rebase-merge")
    write_rebase_head(stack_repo, "apply-branch", state_dir="rebase-apply")
    assert Worktree.from_cwd(stack_repo).rebasing_branch() == "apply-branch"


def test_resolve_working_branch_prefers_checked_out_branch(stack_repo: Path) -> None:
    assert resolve_working_branch(stack_repo, settings=Settings.from_cwd(stack_repo)) == "feature"


def test_resolve_working_branch_prefers_rebase_branch_over_points_at_head(stack_repo: Path) -> None:
    git("branch", "rebasing", "HEAD~1", cwd=stack_repo)
    git("checkout", "--detach", "HEAD", cwd=stack_repo)
    write_rebase_head(stack_repo, "rebasing", state_dir="rebase-merge")

    assert resolve_working_branch(stack_repo, settings=Settings.from_cwd(stack_repo)) == "rebasing"


def test_resolve_rebase_onto_remote_ref(stack_repo: Path) -> None:
    git("update-ref", "refs/remotes/origin/main", "main", cwd=stack_repo)
    assert resolve_rebase_onto_remote_ref(stack_repo, settings=Settings.from_cwd(stack_repo)) == "origin/main"


def test_head_is_linear_on_remote_gerrit_target(stack_repo: Path) -> None:
    m = git_out("rev-parse", "main", cwd=stack_repo)
    git("update-ref", "refs/remotes/origin/main", m, cwd=stack_repo)
    ok, onto = head_is_linear_on_remote_gerrit_target(stack_repo, settings=Settings.from_cwd(stack_repo))
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
    assert resolve_rebase_onto_remote_ref(repo, settings=Settings.from_cwd(repo)) == "origin/main"
    assert effective_gerrit_destination_branch(repo, settings=Settings.from_cwd(repo)) == "origin/main"


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

    assert Settings.from_cwd(repo).branch_gerrit_target("feature") == "main"
    assert effective_gerrit_destination_branch(repo, "feature", settings=Settings.from_cwd(repo)) == "main"
    assert effective_gerrit_destination_branch(repo, "feature", settings=Settings.from_cwd(repo)) != "origin/dev"


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
    assert resolve_rebase_onto_remote_ref(repo, settings=Settings.from_cwd(repo)) == "origin/dev"
