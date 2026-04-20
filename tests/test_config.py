from __future__ import annotations

from pathlib import Path

from gerrit_workflow_tools.config import (
    clear_gerrit_git_config_cache,
    rebase_defaults,
    resolve_rebase_onto_remote_ref,
    warning_patterns,
)
from gerrit_workflow_tools.git_run import git


def test_warning_patterns_defaults(stack_repo: Path) -> None:
    clear_gerrit_git_config_cache()
    assert warning_patterns(stack_repo) == [r"^[^\s]+$", r"(?i:\bwip\b)", r"(?i:\btodo\b)"]


def test_warning_patterns_from_git_config(stack_repo: Path) -> None:
    git("config", "--unset-all", "gerrit.warningPattern", cwd=stack_repo, check=False)
    git("config", "--add", "gerrit.warningPattern", r"^feat:", cwd=stack_repo)
    git("config", "--add", "gerrit.warningPattern", r"^WIP:", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    assert warning_patterns(stack_repo) == [r"^feat:", r"^WIP:"]


def test_rebase_defaults(stack_repo: Path) -> None:
    clear_gerrit_git_config_cache()
    assert rebase_defaults(stack_repo) == {"onto_remote": False, "drop_merged_equivalent": False}
    git("config", "gerrit.rebaseOntoRemote", "true", cwd=stack_repo)
    git("config", "gerrit.rebaseDropMergedEquivalent", "1", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    assert rebase_defaults(stack_repo) == {"onto_remote": True, "drop_merged_equivalent": True}


def test_resolve_rebase_onto_remote_ref(stack_repo: Path) -> None:
    clear_gerrit_git_config_cache()
    git("update-ref", "refs/remotes/origin/main", "main", cwd=stack_repo)
    assert resolve_rebase_onto_remote_ref(stack_repo) == "origin/main"


def test_resolve_rebase_onto_remote_ref_gerrit_target_origin_slash_branch(tmp_path: Path) -> None:
    """gerritTarget origin/dev must not become origin/origin/dev."""
    from gerrit_workflow_tools.config import set_branch_config

    repo = tmp_path / "r"
    repo.mkdir()
    git("init", "-b", "dev2", cwd=repo)
    (repo / "f").write_text("x", encoding="utf-8")
    git("add", "f", cwd=repo)
    git("commit", "-m", "init", cwd=repo)
    git("update-ref", "refs/remotes/origin/dev", "HEAD", cwd=repo)
    set_branch_config(repo, "dev2", gerrit_target="origin/dev")
    clear_gerrit_git_config_cache()
    assert resolve_rebase_onto_remote_ref(repo) == "origin/dev"
