"""Tests for ``ger fetch-api``."""

from __future__ import annotations

from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_fetch_api import main as fetch_api_main
from gerrit_workflow_tools.core.config import ConfigError, Settings
from gerrit_workflow_tools.core.gerrit.rest import resolve_gerrit_web_base
from gerrit_workflow_tools.core.git_run import git
from tests.conftest import run_cli


def _configure_gerrit_http(repo: Path) -> None:
    git("config", "ger.webUrl", "https://g.example.test", cwd=repo)
    git("config", "ger.user", "testuser", cwd=repo)
    git("config", "ger.token", "secrettok", cwd=repo)


def test_fetch_api_missing_weburl(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, _out, err = run_cli(stack_repo, fetch_api_main, ["changes/"], monkeypatch)
    assert code == ExitCode.CONFIG
    assert "ger.webUrl" in err


def test_fetch_api_refuses_a_plain_http_weburl(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Credentials go with every request, so plain http is a config error that names the opt-in."""
    _configure_gerrit_http(stack_repo)
    git("config", "ger.webUrl", "http://g.example.test", cwd=stack_repo)
    code, _out, err = run_cli(stack_repo, fetch_api_main, ["changes/"], monkeypatch)
    assert code == ExitCode.CONFIG
    assert "https://" in err
    assert "ger.allowInsecureHttp" in err


@pytest.mark.parametrize(
    ("web_url", "allow", "expected"),
    [
        ("https://g.example.test/", None, "https://g.example.test"),
        ("HTTPS://g.example.test", None, "HTTPS://g.example.test"),
        ("http://g.example.test:8080/", "true", "http://g.example.test:8080"),
        ("http://g.example.test", "false", None),
        ("ftp://g.example.test", "true", None),
        ("g.example.test", "true", None),
    ],
)
def test_weburl_scheme_policy(web_url: str, allow: str | None, expected: str | None) -> None:
    values = {"ger.webUrl": web_url} | ({"ger.allowInsecureHttp": allow} if allow else {})
    settings = Settings.from_map(values)
    if expected is None:
        with pytest.raises(ConfigError, match="https://"):
            resolve_gerrit_web_base(settings)
    else:
        assert resolve_gerrit_web_base(settings) == expected


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
