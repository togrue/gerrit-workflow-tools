"""Tests for ``ger fetch-api``."""

from __future__ import annotations

from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_fetch_api import main as fetch_api_main
from gerrit_workflow_tools.core.git_run import git
from tests.conftest import run_cli


def _configure_gerrit_http(repo: Path) -> None:
    git("config", "gerrit.webUrl", "https://g.example.test", cwd=repo)
    git("config", "gerrit.user", "testuser", cwd=repo)
    git("config", "gerrit.token", "secrettok", cwd=repo)


def test_fetch_api_missing_weburl(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, _out, err = run_cli(stack_repo, fetch_api_main, ["changes/"], monkeypatch)
    assert code == ExitCode.CONFIG
    assert "gerrit.webUrl" in err


@pytest.mark.parametrize(
    "path",
    ["http://evil.example/x", "//evil.example/x", "changes/../../x", "C:/Program Files/Git/changes/104/detail"],
)
def test_fetch_api_refuses_a_path_that_leaves_the_api(
    path: str, stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_gerrit_http(stack_repo)
    code, _out, err = run_cli(stack_repo, fetch_api_main, [path], monkeypatch)
    assert code == ExitCode.USAGE
    assert "invalid path" in err
