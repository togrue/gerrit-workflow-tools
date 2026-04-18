"""Tests for ``ger edit``."""

from __future__ import annotations

from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_gedit import main as gedit_main
from tests.conftest import run_cli


def test_gedit_help(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, _err = run_cli(stack_repo, gedit_main, ["--help"], monkeypatch, catch_sys_exit=True)
    assert code == 0
    assert " REV\n" in out or out.rstrip().endswith("REV")
    assert "reword" in out.lower()
