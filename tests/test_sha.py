"""Tests for ``ger sha``."""

from __future__ import annotations

from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_sha import main as gsha_main
from tests.conftest import run_cli
from tests.fixtures import _cid


def test_gsha_help(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, _err = run_cli(stack_repo, gsha_main, ["--help"], monkeypatch, catch_sys_exit=True)
    assert code == 0
    assert "CHANGE_ID" in out
    assert "REV_RANGE" in out


def test_gsha_invalid_rev_range_returns_not_found(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, err = run_cli(
        stack_repo,
        gsha_main,
        ["--range", "not-a-real-revision", _cid("1")],
        monkeypatch,
    )
    assert code == ExitCode.NOT_FOUND
    assert out == ""
    assert "error:" in err.lower()


def test_gsha_a_revision_says_what_sha_takes_and_what_to_run(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, _out, err = run_cli(stack_repo, gsha_main, ["HEAD"], monkeypatch)
    assert code == ExitCode.USAGE
    assert err.strip() == (
        "error: invalid Change-Id: 'HEAD'; ger sha takes a full Change-Id (I + 40 hex). "
        "For a commit, change number or other changeish: ger resolve"
    )
