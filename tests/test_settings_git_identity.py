"""Settings-first git identity accessors (no subprocess on the happy path)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit import change_resolution as cr
from gerrit_workflow_tools.core.git_run import _run_git, clear_git_cache, git
from gerrit_workflow_tools.core.git_state import repo_toplevel, resolve_upstream_abbrev_ref


def test_branch_upstream_abbrev_from_refs_heads_merge() -> None:
    settings = Settings.from_map(
        {
            "branch.feature.remote": "origin",
            "branch.feature.merge": "refs/heads/main",
        }
    )
    assert settings.branch_upstream_abbrev("feature") == "origin/main"


def test_branch_upstream_abbrev_nonstandard_merge_returns_none() -> None:
    settings = Settings.from_map(
        {
            "branch.feature.remote": "origin",
            "branch.feature.merge": "refs/remotes/origin/main",
        }
    )
    assert settings.branch_upstream_abbrev("feature") is None


def test_branch_upstream_abbrev_local_dot_remote_returns_none() -> None:
    settings = Settings.from_map(
        {
            "branch.feature.remote": ".",
            "branch.feature.merge": "refs/heads/main",
        }
    )
    assert settings.branch_upstream_abbrev("feature") is None


def test_resolve_upstream_abbrev_ref_settings_first(tmp_path: Path) -> None:
    settings = Settings.from_map(
        {
            "branch.feature.remote": "origin",
            "branch.feature.merge": "refs/heads/main",
        }
    )

    def boom(*_a: object, **_k: object) -> None:
        raise AssertionError("git should not run when branch upstream is in Settings")

    with patch("gerrit_workflow_tools.core.git_state.git", side_effect=boom):
        assert resolve_upstream_abbrev_ref(tmp_path, "feature", settings=settings) == "origin/main"


def test_worktree_memo_survives_non_cacheable_git(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git("init", "-b", "main", cwd=repo)
    (repo / "f").write_text("x\n", encoding="utf-8")
    git("add", "f", cwd=repo)
    git("commit", "-m", "init", cwd=repo)

    clear_git_cache()
    with patch("gerrit_workflow_tools.core.git_run._run_git", wraps=_run_git) as run:
        first = repo_toplevel(repo)
        git("remote", "add", "origin", repo.as_uri(), cwd=repo)
        second = repo_toplevel(repo)
    assert first == second == repo.resolve()
    combined = [c for c in run.call_args_list if c.args and c.args[0] == "rev-parse" and "--show-toplevel" in c.args]
    assert len(combined) == 1


def test_resolve_stack_context_memoized(tmp_path: Path) -> None:
    settings = Settings.from_map(
        {
            "gerrit.project": "proj",
            "branch.feature.remote": "origin",
            "branch.feature.merge": "refs/heads/main",
            "branch.feature.gerrittarget": "main",
        }
    )
    calls = {"n": 0}
    original = cr._resolve_stack_context_uncached

    def counting(*args: object, **kwargs: object) -> cr.StackContext:
        calls["n"] += 1
        return original(*args, **kwargs)  # type: ignore[arg-type]

    with patch.object(cr, "_resolve_stack_context_uncached", side_effect=counting):
        a = cr.resolve_stack_context(tmp_path, branch="feature", settings=settings)
        b = cr.resolve_stack_context(tmp_path, branch="feature", settings=settings)
    assert a == b
    assert calls["n"] == 1
