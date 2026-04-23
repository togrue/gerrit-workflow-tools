"""Tests for ``ger edit``."""

from __future__ import annotations

from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_edit import main as gedit_main
from gerrit_workflow_tools.cli_edit import main_reword as greword_main
from tests.conftest import run_cli


def test_gedit_help(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, _err = run_cli(stack_repo, gedit_main, ["--help"], monkeypatch, catch_sys_exit=True)
    assert code == 0
    assert " REV\n" in out or out.rstrip().endswith("REV")
    assert "reword" in out.lower()


def test_greword_help(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, _err = run_cli(stack_repo, greword_main, ["--help"], monkeypatch, catch_sys_exit=True)
    assert code == 0
    assert "ger reword" in out
    assert "--edit" in out
    assert "--drop" in out
