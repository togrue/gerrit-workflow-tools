from __future__ import annotations

from pathlib import Path

from gerrit_workflow_tools.core.config import (
    _DEFAULT_STOP_PATTERN,
    _DEFAULT_WARNING_PATTERN,
    clear_gerrit_git_config_cache,
    gerrit_push_remote_policy,
    rebase_defaults,
    stop_pattern,
    warning_pattern,
)
from gerrit_workflow_tools.core.git_run import git


def test_stop_pattern_defaults(stack_repo: Path) -> None:
    clear_gerrit_git_config_cache()
    assert stop_pattern(stack_repo) == _DEFAULT_STOP_PATTERN


def test_stop_pattern_from_git_config(stack_repo: Path) -> None:
    git("config", "gerrit.stopPattern", r"^hold:", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    assert stop_pattern(stack_repo) == r"^hold:"


def test_warning_pattern_defaults(stack_repo: Path) -> None:
    clear_gerrit_git_config_cache()
    assert warning_pattern(stack_repo) == _DEFAULT_WARNING_PATTERN


def test_warning_pattern_from_git_config(stack_repo: Path) -> None:
    git("config", "gerrit.warningPattern", r"^feat:", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    assert warning_pattern(stack_repo) == r"^feat:"


def test_rebase_defaults(stack_repo: Path) -> None:
    clear_gerrit_git_config_cache()
    assert rebase_defaults(stack_repo) == {"onto_remote": False, "drop_merged_equivalent": False}
    git("config", "gerrit.rebaseOntoRemote", "true", cwd=stack_repo)
    git("config", "gerrit.rebaseDropMergedEquivalent", "1", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    assert rebase_defaults(stack_repo) == {"onto_remote": True, "drop_merged_equivalent": True}


def test_gerrit_push_remote_policy_defaults_and_aliases(stack_repo: Path) -> None:
    clear_gerrit_git_config_cache()
    assert gerrit_push_remote_policy(stack_repo) == "ignore-not-rebased"
    git("config", "gerrit.push.remotePolicy", "WARN-NOT-REBASED", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    assert gerrit_push_remote_policy(stack_repo) == "warn-not-rebased"
    git("config", "gerrit.push.remotePolicy", "bogus", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    assert gerrit_push_remote_policy(stack_repo) == "ignore-not-rebased"
