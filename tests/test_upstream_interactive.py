from __future__ import annotations

import sys
from pathlib import Path

import pytest

from gerrit_workflow_tools.core.git_run import git, git_out
from gerrit_workflow_tools.core.upstream_interactive import (
    branch_has_upstream,
    ensure_branch_upstream_interactive,
    read_recent_upstream_abbrevs,
)


class _StdinTTY:
    def isatty(self) -> bool:
        return True


class _StdinNonTTY:
    def isatty(self) -> bool:
        return False


def _make_repo_with_origin(path: Path) -> Path:
    env = {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    bare = path / "upstream.git"
    git("init", "--bare", str(bare), env=env)
    repo = path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    git("init", "-b", "main", cwd=repo, env=env)
    (repo / "README.md").write_text("init\n", encoding="utf-8")
    git("add", "README.md", cwd=repo, env=env)
    git("commit", "-m", "init", cwd=repo, env=env)
    git("remote", "add", "origin", str(bare), cwd=repo, env=env)
    git("push", "-u", "origin", "main", cwd=repo, env=env)
    git("checkout", "-b", "feature", cwd=repo, env=env)
    (repo / "README.md").write_text("feature\n", encoding="utf-8")
    git("add", "README.md", cwd=repo, env=env)
    git("commit", "-m", "feature", cwd=repo, env=env)
    git("fetch", "origin", cwd=repo, env=env)
    git("branch", "--unset-upstream", "feature", cwd=repo, env=env, check=False)
    return repo


def test_ensure_branch_upstream_interactive_sets_upstream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _make_repo_with_origin(tmp_path)
    assert not branch_has_upstream(repo, "feature")
    monkeypatch.setattr(sys, "stdin", _StdinTTY())
    monkeypatch.setattr(
        "gerrit_workflow_tools.core.upstream_interactive.prompt_upstream_abbrev_interactive",
        lambda _cwd, _branch: "origin/main",
    )
    assert ensure_branch_upstream_interactive(repo, "feature")
    assert git_out("rev-parse", "--abbrev-ref", "@{upstream}", cwd=repo) == "origin/main"
    assert "origin/main" in read_recent_upstream_abbrevs(repo)


def test_ensure_branch_upstream_interactive_non_tty_skips_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _make_repo_with_origin(tmp_path)
    assert not branch_has_upstream(repo, "feature")
    monkeypatch.setattr(sys, "stdin", _StdinNonTTY())

    def _boom(_cwd: Path | str | None, _branch: str) -> str | None:
        raise AssertionError("prompt must not run without tty")

    monkeypatch.setattr("gerrit_workflow_tools.core.upstream_interactive.prompt_upstream_abbrev_interactive", _boom)
    assert not ensure_branch_upstream_interactive(repo, "feature")
