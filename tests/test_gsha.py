"""Tests for ``git gsha``."""

from __future__ import annotations

from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_gsha import main as gsha_main
from tests.conftest import run_cli


def test_gsha_help(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, _err = run_cli(
        stack_repo, gsha_main, ["--help"], monkeypatch, catch_sys_exit=True
    )
    assert code == 0
    assert "CHANGE_ID" in out
    assert "REV_RANGE" in out
