"""Tests for ``ger setup``."""

from __future__ import annotations

from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_ger import main as ger_main
from gerrit_workflow_tools.cli_setup import _validate_web_url
from gerrit_workflow_tools.core.git_run import git
from tests.conftest import run_cli


def test_validate_web_url_accepts_https_base() -> None:
    assert _validate_web_url("https://gerrit.example.com/") is None


def test_validate_web_url_rejects_missing_scheme() -> None:
    assert _validate_web_url("gerrit.example.com") is not None


def test_validate_web_url_rejects_path() -> None:
    assert _validate_web_url("https://gerrit.example.com/admin") is not None


def test_ger_setup_help_delegates(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, _err = run_cli(stack_repo, ger_main, ["setup", "--help"], monkeypatch, catch_sys_exit=True)
    assert code == 0
    assert "ger setup" in out
    assert "gerrit.webUrl" in out


def test_ger_setup_non_tty_reports_error(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    code, _out, err = run_cli(stack_repo, ger_main, ["setup"], monkeypatch)
    assert code == 1
    assert "interactive terminal" in err.lower()
    assert "gerrit.webUrl" in err


def test_ger_setup_writes_global_config(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inputs = iter(
        [
            "https://gerrit.example.com",
            "alice",
            "secret-token",
        ]
    )

    def _fake_prompt_session(*_args, **_kwargs):
        class _Session:
            def prompt(self, *_a, **_kw) -> str:
                return next(inputs)

        return _Session()

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("prompt_toolkit.PromptSession", _fake_prompt_session)

    code, _out, err = run_cli(stack_repo, ger_main, ["setup"], monkeypatch)
    assert code == 0
    assert "gerrit.webUrl='https://gerrit.example.com'" in err
    assert "gerrit.user='alice'" in err

    assert git("config", "--global", "--get", "gerrit.webUrl", cwd=stack_repo).stdout.strip() == (
        "https://gerrit.example.com"
    )
    assert git("config", "--global", "--get", "gerrit.user", cwd=stack_repo).stdout.strip() == "alice"
    assert git("config", "--global", "--get", "gerrit.token", cwd=stack_repo).stdout.strip() == "secret-token"


def test_ger_setup_local_flag_writes_repo_config(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inputs = iter(["https://gerrit.local.test", "bob", "tok"])

    def _fake_prompt_session(*_args, **_kwargs):
        class _Session:
            def prompt(self, *_a, **_kw) -> str:
                return next(inputs)

        return _Session()

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("prompt_toolkit.PromptSession", _fake_prompt_session)

    code, _out, _err = run_cli(stack_repo, ger_main, ["setup", "--local"], monkeypatch)
    assert code == 0
    assert git("config", "--get", "gerrit.webUrl", cwd=stack_repo).stdout.strip() == "https://gerrit.local.test"
    assert git("config", "--get", "gerrit.user", cwd=stack_repo).stdout.strip() == "bob"
    assert git("config", "--get", "gerrit.token", cwd=stack_repo).stdout.strip() == "tok"
