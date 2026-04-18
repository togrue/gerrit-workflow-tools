"""Tests for the unified ``ger`` CLI dispatcher."""

from __future__ import annotations

from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_ger import main as ger_main
from tests.conftest import run_cli


def test_ger_no_args_prints_usage(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, _err = _run_ger(stack_repo, [], monkeypatch)
    assert code == 2
    assert "ger <command>" in out
    assert "log" in out


def test_ger_help(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, _err = _run_ger(stack_repo, ["--help"], monkeypatch)
    assert code == 0
    assert "ger <command>" in out


def test_ger_unknown_command(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, _out, err = _run_ger(stack_repo, ["nope"], monkeypatch)
    assert code == 1
    assert "unknown command" in err


def test_ger_log_help_delegates(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, _err = _run_ger(stack_repo, ["log", "--help"], monkeypatch, catch_sys_exit=True)
    assert code == 0
    assert "ger log" in out


def _run_ger(
    cwd: Path,
    argv: list[str],
    monkeypatch: pytest.MonkeyPatch,
    *,
    catch_sys_exit: bool = False,
) -> tuple[int, str, str]:
    return run_cli(cwd, ger_main, argv, monkeypatch, catch_sys_exit=catch_sys_exit)
