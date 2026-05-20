"""Tests for git_run caching and error handling."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from gerrit_workflow_tools.core.git_run import GitError, _run_git, clear_git_cache, git, git_out


@pytest.fixture(autouse=True)
def _clear_git_cache() -> None:
    clear_git_cache()
    yield
    clear_git_cache()


def test_rev_parse_cached(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git("init", "-b", "main", cwd=repo)
    (repo / "f").write_text("x\n", encoding="utf-8")
    git("add", "f", cwd=repo)
    git("commit", "-m", "init", cwd=repo)

    with patch("gerrit_workflow_tools.core.git_run._run_git", wraps=_run_git) as run:
        a = git_out("rev-parse", "HEAD", cwd=repo)
        b = git_out("rev-parse", "HEAD", cwd=repo)
    assert a == b
    assert run.call_count == 1


def test_log_cached(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git("init", "-b", "main", cwd=repo)
    (repo / "f").write_text("x\n", encoding="utf-8")
    git("add", "f", cwd=repo)
    git("commit", "-m", "init", cwd=repo)

    with patch("gerrit_workflow_tools.core.git_run._run_git", wraps=_run_git) as run:
        git("log", "-1", "--format=%s", cwd=repo)
        git("log", "-1", "--format=%s", cwd=repo)
    assert run.call_count == 1


def test_mutating_commands_not_cached(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git("init", "-b", "main", cwd=repo)

    with patch("gerrit_workflow_tools.core.git_run._run_git", wraps=git.__globals__["_run_git"]) as run:
        git("status", cwd=repo, check=False)
        git("status", cwd=repo, check=False)
    assert run.call_count == 2


def test_cache_respects_check_flag(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git("init", "-b", "main", cwd=repo)

    with patch("gerrit_workflow_tools.core.git_run._run_git", wraps=_run_git) as run:
        p = git("rev-parse", "--verify", "nope", cwd=repo, check=False)
        assert p.returncode != 0
        with pytest.raises(GitError):
            git("rev-parse", "--verify", "nope", cwd=repo, check=True)
    assert run.call_count == 1


def test_cache_key_includes_env_override(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git("init", "-b", "main", cwd=repo)
    (repo / "f").write_text("x\n", encoding="utf-8")
    git("add", "f", cwd=repo)
    git("commit", "-m", "init", cwd=repo)

    with patch("gerrit_workflow_tools.core.git_run._run_git", wraps=_run_git) as run:
        git_out("rev-parse", "HEAD", cwd=repo, env={"_GER_TEST": "a"})
        git_out("rev-parse", "HEAD", cwd=repo, env={"_GER_TEST": "b"})
    assert run.call_count == 2
