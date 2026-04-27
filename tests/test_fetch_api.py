"""Tests for ``ger fetch-api``."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gerrit_workflow_tools.cli_fetch_api import main as fetch_api_main
from gerrit_workflow_tools.config import clear_gerrit_git_config_cache
from gerrit_workflow_tools.core.git_run import git
from tests.conftest import json_stdout, run_cli


def _configure_gerrit_http(repo: Path) -> None:
    git("config", "gerrit.webUrl", "https://g.example.test", cwd=repo)
    git("config", "gerrit.user", "testuser", cwd=repo)
    git("config", "gerrit.token", "secrettok", cwd=repo)
    clear_gerrit_git_config_cache()


def test_fetch_api_prints_json(
    stack_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_gerrit_http(stack_repo)

    mock_client = MagicMock()
    mock_client.get_json.return_value = {"_number": 1, "subject": "hi"}

    monkeypatch.setattr(
        "gerrit_workflow_tools.cli_fetch_api.GerritClient",
        lambda *a, **k: mock_client,
    )

    code, out, err = run_cli(stack_repo, fetch_api_main, ["accounts/self/detail"], monkeypatch)
    assert code == 0
    assert err == ""
    assert json_stdout(out) == {"_number": 1, "subject": "hi"}
    mock_client.get_json.assert_called_once_with("accounts/self/detail")


def test_fetch_api_missing_weburl(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, _out, err = run_cli(stack_repo, fetch_api_main, ["changes/"], monkeypatch)
    assert code == 1
    assert "gerrit.webUrl" in err


def test_fetch_api_gerrit_error(
    stack_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gerrit_workflow_tools.gerrit_client import GerritApiError

    _configure_gerrit_http(stack_repo)

    mock_client = MagicMock()
    mock_client.get_json.side_effect = GerritApiError("Gerrit HTTP 404", status=404)

    monkeypatch.setattr(
        "gerrit_workflow_tools.cli_fetch_api.GerritClient",
        lambda *a, **k: mock_client,
    )

    code, _out, err = run_cli(stack_repo, fetch_api_main, ["changes/nope"], monkeypatch)
    assert code == 1
    assert "404" in err
