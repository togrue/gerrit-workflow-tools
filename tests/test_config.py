from __future__ import annotations

from pathlib import Path

from gerrit_workflow_tools.config import clear_gerrit_git_config_cache, warning_patterns
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
