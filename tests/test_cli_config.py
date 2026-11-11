"""The ger config temp-file editor: render, edit, apply."""

from __future__ import annotations

from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_ger import main as ger_main
from gerrit_workflow_tools.core.git_run import git
from tests.conftest import run_cli


def test_ger_config_help(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, _err = run_cli(stack_repo, ger_main, ["config", "--help"], monkeypatch, catch_sys_exit=True)
    assert code == 0
    assert "ger config" in out
    assert "key=value" in out


def test_ger_config_refuses_a_non_tty(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, _out, err = run_cli(stack_repo, ger_main, ["config"], monkeypatch)
    assert code == 2
    assert "interactive terminal" in err


def test_ger_config_applies_editor_changes(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_editor(path: Path, cwd, *, settings=None, env_overrides=()) -> int:
        text = Path(path).read_text(encoding="utf-8")
        Path(path).write_text(text.replace("# gerrit.remote=origin", "gerrit.remote=upstream"), encoding="utf-8")
        return 0

    monkeypatch.setattr("gerrit_workflow_tools.cli_config._stream_is_tty", lambda _stream: True)
    monkeypatch.setattr("gerrit_workflow_tools.cli_config.run_git_editor", _fake_editor)
    code, out, err = run_cli(stack_repo, ger_main, ["config"], monkeypatch)
    assert code == 0, err
    assert "set gerrit.remote=upstream" in out
    assert git("config", "--local", "--get", "gerrit.remote", cwd=stack_repo).stdout.strip() == "upstream"


def test_ger_config_empty_file_aborts(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _empty_editor(path: Path, cwd, *, settings=None, env_overrides=()) -> int:
        Path(path).write_text("", encoding="utf-8")
        return 0

    monkeypatch.setattr("gerrit_workflow_tools.cli_config._stream_is_tty", lambda _stream: True)
    monkeypatch.setattr("gerrit_workflow_tools.cli_config.run_git_editor", _empty_editor)
    code, _out, err = run_cli(stack_repo, ger_main, ["config"], monkeypatch)
    assert code == 1
    assert "Aborted" in err
    assert git("config", "--local", "--get", "gerrit.remote", cwd=stack_repo, check=False).returncode != 0
