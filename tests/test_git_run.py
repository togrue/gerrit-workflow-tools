"""Tests for running git: decoding, errors, and that every call runs git."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from gerrit_workflow_tools.core.git_run import GitError, _run_git, git, git_out


def test_git_output_is_decoded_as_utf8_whatever_the_locale(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git("init", "-b", "main", cwd=repo)
    (repo / "f").write_text("x", encoding="utf-8")
    git("add", "f", cwd=repo)
    git("commit", "-m", "feat: ünï ✓ 日本", cwd=repo)

    real_run = subprocess.run

    def as_cp1252_locale(*args: Any, **kwargs: Any) -> Any:
        return real_run(*args, **{"encoding": "cp1252", **kwargs})

    with patch("subprocess.run", as_cp1252_locale):
        assert git("log", "-1", "--format=%s", cwd=repo, check=False).stdout.strip() == "feat: ünï ✓ 日本"


def test_git_error_prints_git_stderr_once_without_its_hint(tmp_path: Path) -> None:
    git("init", "-b", "main", cwd=tmp_path)
    with pytest.raises(GitError) as raised:
        git("rev-parse", "nosuch", cwd=tmp_path)
    text = str(raised.value)
    assert text.count("ambiguous argument") == 1
    assert "Use '--'" not in text


def test_every_call_runs_git(tmp_path: Path) -> None:
    """Nothing remembers an answer: the same read twice is two git runs, the second seeing any change."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git("init", "-b", "main", cwd=repo)
    git("commit", "--allow-empty", "-m", "one", cwd=repo)

    with patch("gerrit_workflow_tools.core.git_run._run_git", wraps=_run_git) as run:
        first = git_out("rev-parse", "HEAD", cwd=repo)
        git("commit", "--allow-empty", "-m", "two", cwd=repo)
        second = git_out("rev-parse", "HEAD", cwd=repo)
    assert first != second
    assert run.call_count == 3
