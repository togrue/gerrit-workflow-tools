"""The Gerrit token is tied to its host (#111): ``ger.<url>.token`` goes only to that ``ger.webUrl``."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_fetch_api import main as fetch_api_main
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.config_keys import ConfigScope
from gerrit_workflow_tools.core.gerrit.credentials import GerritAuth, gerrit_auth_from_settings
from gerrit_workflow_tools.core.gerrit.event_source import discover_ssh_target
from gerrit_workflow_tools.core.gerrit.http_rest import HttpGerritRest
from gerrit_workflow_tools.core.git_run import git
from tests.conftest import run_cli


HOST = "https://gerrit.example.com"
SCOPED = {f"ger.{HOST}.user": "alice", f"ger.{HOST}.token": "tok"}


def test_a_host_scoped_token_goes_to_its_host_only(config_warnings: Callable[[], list[str]]) -> None:
    here = Settings.from_map({"ger.webUrl": f"{HOST}/", **SCOPED})
    assert gerrit_auth_from_settings(here) == GerritAuth("alice", "tok")
    assert discover_ssh_target(here).user == "alice"
    elsewhere = Settings.from_map({"ger.webUrl": "https://evil.example.net", **SCOPED})
    assert gerrit_auth_from_settings(elsewhere) is None
    assert gerrit_auth_from_settings(elsewhere, HOST) == GerritAuth("alice", "tok")
    assert config_warnings() == []


def test_the_scoped_secret_wins_and_the_user_falls_back(config_warnings: Callable[[], list[str]]) -> None:
    settings = Settings.from_map(
        {"ger.webUrl": HOST, "ger.user": "bob", "ger.token": "anywhere", f"ger.{HOST}.password": "pw"}
    )
    assert gerrit_auth_from_settings(settings) == GerritAuth("bob", "pw")
    assert config_warnings() == []


def test_an_unscoped_token_still_works_and_warns_once_with_the_move(config_warnings: Callable[[], list[str]]) -> None:
    settings = Settings.from_map(
        {"ger.webUrl": HOST, "ger.user": "bob", "ger.token": "tok"}, scopes={"ger.token": ConfigScope.GLOBAL}
    )
    assert gerrit_auth_from_settings(settings) == GerritAuth("bob", "tok")
    assert gerrit_auth_from_settings(settings) == GerritAuth("bob", "tok")
    assert config_warnings() == [
        "git config key ger.token is sent to whatever host ger.webUrl names; move it with: "
        f'git config --global ger.{HOST}.token "$(git config --global ger.token)" '
        "&& git config --global --unset ger.token"
    ]


def test_a_refusal_names_the_keys_the_credentials_came_from(
    config_warnings: Callable[[], list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 401 names the keys in use: the plain ones when those were sent, not the per-host ones."""
    from urllib.error import HTTPError

    from gerrit_workflow_tools.core.gerrit import http_rest
    from gerrit_workflow_tools.core.gerrit.rest import GerritUnavailableError

    def _refuse(*_args: object, **_kwargs: object) -> object:
        raise HTTPError(f"{HOST}/a/changes/", 401, "Unauthorized", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(http_rest, "urlopen", _refuse)
    plain = Settings.from_map({"ger.webUrl": HOST, "ger.user": "bob", f"ger.{HOST}.password": "pw"})
    with pytest.raises(GerritUnavailableError) as caught:
        HttpGerritRest.from_settings(plain).query_changes("status:open")
    assert f"check ger.user and ger.{HOST}.password, or run `ger setup`" in str(caught.value)
    assert caught.value.reason == "credentials"
    del config_warnings


def test_a_command_sends_only_its_hosts_token(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Through ``ger fetch-api``, with the keys as git writes them."""
    for key, value in SCOPED.items():
        git("config", key, value, cwd=stack_repo)
    sent: list[str] = []

    def _get_json(self: HttpGerritRest, path: str, **_kw: object) -> object:
        sent.append(self._auth_headers()["Authorization"])  # raises without credentials, as a real request does
        return {}

    monkeypatch.setattr(HttpGerritRest, "get_json", _get_json)
    git("config", "ger.webUrl", HOST, cwd=stack_repo)
    code, _out, _err = run_cli(stack_repo, fetch_api_main, ["changes/"], monkeypatch)
    assert (code, sent) == (ExitCode.OK, [GerritAuth("alice", "tok").header()])

    git("config", "ger.webUrl", "https://other.example.test", cwd=stack_repo)
    code, _out, err = run_cli(stack_repo, fetch_api_main, ["changes/"], monkeypatch)
    assert code != ExitCode.OK
    assert "missing Gerrit credentials for https://other.example.test" in err
    assert len(sent) == 1
