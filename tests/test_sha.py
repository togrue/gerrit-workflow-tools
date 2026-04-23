"""Tests for ``ger sha``."""

from __future__ import annotations

from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_sha import main as gsha_main
from tests.conftest import run_cli
from tests.fixtures import _cid


def test_gsha_help(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, _err = run_cli(stack_repo, gsha_main, ["--help"], monkeypatch, catch_sys_exit=True)
    assert code == 0
    assert "CHANGE_ID" in out
    assert "REV_RANGE" in out


def test_gsha_invalid_rev_range_returns_git_error(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, err = run_cli(
        stack_repo,
        gsha_main,
        ["--range", "not-a-real-revision", _cid("1")],
        monkeypatch,
    )
    assert code == 4
    assert out == ""
    assert "error:" in err.lower()
