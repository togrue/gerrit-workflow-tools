from __future__ import annotations

import sys
from pathlib import Path

import pytest

from gerrit_workflow_tools.core.config import ConfigError, Settings
from gerrit_workflow_tools.core.git_run import git, git_out
from gerrit_workflow_tools.core.git_state import is_detached_head
from gerrit_workflow_tools.core.upstream import branch_has_upstream, read_recent_upstream_abbrevs
from gerrit_workflow_tools.core.upstream_interactive import (
    ensure_branch_upstream_interactive,
    prompt_upstream_abbrev_interactive,
    require_branch_upstream,
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
        lambda _cwd, _branch, **_kw: "origin/main",
    )
    assert ensure_branch_upstream_interactive(repo, "feature", settings=Settings.from_cwd(repo))
    assert git_out("rev-parse", "--abbrev-ref", "@{upstream}", cwd=repo) == "origin/main"
    assert "origin/main" in read_recent_upstream_abbrevs(repo)


def test_ensure_branch_upstream_interactive_detached_skips_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _make_repo_with_origin(tmp_path)
    git("checkout", "--detach", "HEAD", cwd=repo)
    assert is_detached_head(repo)
    monkeypatch.setattr(sys, "stdin", _StdinTTY())
    monkeypatch.setattr(
        "gerrit_workflow_tools.core.upstream_interactive.prompt_upstream_abbrev_interactive",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("prompt must not run on detached HEAD")),
    )
    assert not ensure_branch_upstream_interactive(repo, "feature", settings=Settings.from_cwd(repo))


def test_ensure_branch_upstream_interactive_non_tty_skips_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _make_repo_with_origin(tmp_path)
    assert not branch_has_upstream(repo, "feature")
    monkeypatch.setattr(sys, "stdin", _StdinNonTTY())

    def _boom(_cwd: Path | str | None, _branch: str) -> str | None:
        raise AssertionError("prompt must not run without tty")

    monkeypatch.setattr("gerrit_workflow_tools.core.upstream_interactive.prompt_upstream_abbrev_interactive", _boom)
    assert not ensure_branch_upstream_interactive(repo, "feature", settings=Settings.from_cwd(repo))


def test_require_branch_upstream_non_tty_prints_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _make_repo_with_origin(tmp_path)
    assert not branch_has_upstream(repo, "feature")
    monkeypatch.setattr(sys, "stdin", _StdinNonTTY())

    with pytest.raises(ConfigError) as caught:
        require_branch_upstream(repo, "feature", settings=Settings.from_cwd(repo))

    assert "No upstream configured for branch 'feature'" in str(caught.value)
    assert "git branch --set-upstream-to=" in str(caught.value)
    assert capsys.readouterr().err == ""


def test_upstream_prompt_refuses_a_local_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _make_repo_with_origin(tmp_path)
    monkeypatch.setattr("prompt_toolkit.PromptSession.prompt", lambda *_a, **_k: "main")
    monkeypatch.setattr("prompt_toolkit.PromptSession.__init__", lambda *_a, **_k: None)

    assert prompt_upstream_abbrev_interactive(repo, "feature", settings=Settings.from_cwd(repo)) is None


def test_upstream_prompt_accepts_a_remote_tracking_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _make_repo_with_origin(tmp_path)
    monkeypatch.setattr("prompt_toolkit.PromptSession.prompt", lambda *_a, **_k: "origin/main")
    monkeypatch.setattr("prompt_toolkit.PromptSession.__init__", lambda *_a, **_k: None)

    assert prompt_upstream_abbrev_interactive(repo, "feature", settings=Settings.from_cwd(repo)) == "origin/main"


def test_missing_upstream_without_a_console_reports_instead_of_prompting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _make_repo_with_origin(tmp_path)
    monkeypatch.setattr(sys, "stdin", _StdinTTY())
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr("gerrit_workflow_tools.core.upstream_interactive._is_windows_console", lambda _stream: False)

    with pytest.raises(ConfigError, match="No upstream configured"):
        require_branch_upstream(repo, "feature", settings=Settings.from_cwd(repo))
