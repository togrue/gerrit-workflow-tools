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
    assert "ger.webUrl" in out


def test_ger_setup_non_tty_reports_error(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    code, _out, err = run_cli(stack_repo, ger_main, ["setup"], monkeypatch)
    assert code == 2
    assert "interactive terminal" in err.lower()
    assert "ger.webUrl" in err


def test_ger_setup_treats_windows_nul_as_non_interactive(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr("gerrit_workflow_tools.core.upstream_interactive._is_windows_console", lambda _stream: False)

    code, _out, err = run_cli(stack_repo, ger_main, ["setup"], monkeypatch)

    assert code == 2
    assert "interactive terminal" in err.lower()


def _scripted_setup(monkeypatch: pytest.MonkeyPatch, answers: list[str]) -> None:
    inputs = iter(answers)

    class _Session:
        def prompt(self, *_a, **_kw) -> str:
            return next(inputs)

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("prompt_toolkit.PromptSession", lambda *_a, **_kw: _Session())


def test_ger_setup_writes_global_config(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _scripted_setup(monkeypatch, ["https://gerrit.example.com", "alice", "secret-token"])

    code, _out, err = run_cli(stack_repo, ger_main, ["setup"], monkeypatch)
    assert code == 0
    assert "ger.webUrl='https://gerrit.example.com'" in err
    assert "ger.https://gerrit.example.com.user='alice'" in err

    assert git("config", "--global", "--get", "ger.webUrl", cwd=stack_repo).stdout.strip() == (
        "https://gerrit.example.com"
    )
    host = "ger.https://gerrit.example.com"
    assert git("config", "--global", "--get", f"{host}.user", cwd=stack_repo).stdout.strip() == "alice"
    assert git("config", "--global", "--get", f"{host}.token", cwd=stack_repo).stdout.strip() == "secret-token"
    assert git("config", "--global", "--get", "ger.token", cwd=stack_repo, check=False).returncode != 0


def test_ger_setup_local_flag_writes_repo_config(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _scripted_setup(monkeypatch, ["https://gerrit.local.test", "bob", "tok"])

    code, _out, _err = run_cli(stack_repo, ger_main, ["setup", "--local"], monkeypatch)
    assert code == 0
    assert git("config", "--get", "ger.webUrl", cwd=stack_repo).stdout.strip() == "https://gerrit.local.test"
    assert git("config", "--get", "ger.https://gerrit.local.test.user", cwd=stack_repo).stdout.strip() == "bob"
    assert git("config", "--get", "ger.https://gerrit.local.test.token", cwd=stack_repo).stdout.strip() == "tok"


def test_ger_setup_moves_an_unscoped_token_to_the_host(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An existing unscoped ger.token is kept on Enter, written for the host, and removed (#111)."""
    git("config", "--global", "ger.user", "carol", cwd=stack_repo)
    git("config", "--global", "gerrit.token", "old-tok", cwd=stack_repo)
    _scripted_setup(monkeypatch, ["https://gerrit.example.com", "carol", ""])

    code, _out, err = run_cli(stack_repo, ger_main, ["setup"], monkeypatch)

    assert code == 0
    assert "Moved the unscoped ger.token" in err
    host = "ger.https://gerrit.example.com"
    assert git("config", "--global", "--get", f"{host}.token", cwd=stack_repo).stdout.strip() == "old-tok"
    for key in ("ger.token", "gerrit.token"):
        assert git("config", "--global", "--get", key, cwd=stack_repo, check=False).returncode != 0


def test_ger_setup_rerun_offers_the_hosts_token(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "--global", "ger.https://gerrit.example.com.token", "kept", cwd=stack_repo)
    _scripted_setup(monkeypatch, ["https://gerrit.example.com", "dave", ""])

    code, _out, err = run_cli(stack_repo, ger_main, ["setup"], monkeypatch)

    assert code == 0
    assert "Moved" not in err
    assert git(
        "config", "--global", "--get", "ger.https://gerrit.example.com.token", cwd=stack_repo
    ).stdout.strip() == ("kept")


def test_ger_setup_moves_an_unscoped_password_to_the_host(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An unscoped password left in place would still go to any host; it moves to the host's key."""
    git("config", "--global", "gerrit.password", "pw", cwd=stack_repo)
    _scripted_setup(monkeypatch, ["https://gerrit.example.com", "erin", "tok"])

    code, _out, err = run_cli(stack_repo, ger_main, ["setup"], monkeypatch)

    assert code == 0
    assert "Moved the unscoped ger.password" in err
    host = "ger.https://gerrit.example.com"
    assert git("config", "--global", "--get", f"{host}.password", cwd=stack_repo).stdout.strip() == "pw"
    for key in ("ger.password", "gerrit.password"):
        assert git("config", "--global", "--get", key, cwd=stack_repo, check=False).returncode != 0
