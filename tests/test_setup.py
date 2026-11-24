"""Tests for ``ger setup``: nothing is saved until Gerrit accepts it, and a terminal is not required.

Before, a URL without a scheme ended setup with an error, and an http URL with a wrong token was
reported "Configured" with exit 0: the next command failed instead.
"""

from __future__ import annotations

import io
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_ger import main as ger_main
from gerrit_workflow_tools.cli_setup import main as setup_main
from gerrit_workflow_tools.core.gerrit.rest import GerritUnavailableError
from gerrit_workflow_tools.core.gerrit_setup import web_url_problem
from gerrit_workflow_tools.core.git_run import git, git_out
from tests.change_store import ChangeStore
from tests.conftest import run_cli


ALICE = {"_account_id": 1000, "name": "Alice Reviewer", "email": "alice@example.com", "username": "alice"}


def _store() -> ChangeStore:
    return ChangeStore(accounts={1000: ALICE}, self_account_id=1000)


class _Rejecting(ChangeStore):
    """Gerrit refusing the credentials, as the HTTP seam reports a 401."""

    def get_account(self, account_id: int | str) -> dict[str, Any]:
        self._record("get_account", account_id)
        raise GerritUnavailableError("Gerrit at g.example rejected the credentials (HTTP 401)", status=401)


def _config(repo: Path, key: str, *scope: str) -> str | None:
    found = git("config", *scope, "--get", key, cwd=repo, check=False)
    return found.stdout.strip() if found.returncode == 0 else None


def _hook(repo: Path) -> Path:
    return Path(git_out("rev-parse", "--path-format=absolute", "--git-path", "hooks/commit-msg", cwd=repo))


def _setup(repo: Path, argv: list[str], monkeypatch: pytest.MonkeyPatch, store: ChangeStore | None = None):
    def run(argv: list[str] | None = None) -> int:
        return setup_main(argv, gerrit=store or _store())

    return run_cli(repo, run, argv, monkeypatch, catch_sys_exit=True)


def _answers(monkeypatch: pytest.MonkeyPatch, prompts: list[str], confirms: list[str] = ()) -> Iterator[str]:  # type: ignore[assignment]
    """A terminal whose prompts get *prompts*, and whose yes/no questions get *confirms*."""
    answers = iter(prompts)
    replies = iter(confirms)

    def _session(*_args: object, **_kwargs: object) -> object:
        class _Session:
            def prompt(self, *_a: object, **_kw: object) -> str:
                return next(answers)

        return _Session()

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("prompt_toolkit.PromptSession", _session)
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(replies))
    return answers


def test_web_url_problem_accepts_a_base_url_and_names_what_is_wrong() -> None:
    assert web_url_problem("https://gerrit.example.com/") is None
    assert web_url_problem("gerrit.example.com:8081") == (
        "the web URL must start with https:// (or http:// for a test Gerrit): try https://gerrit.example.com:8081"
    )
    assert web_url_problem("https://gerrit.example.com/admin") is not None


def test_ger_setup_help_delegates(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, _err = run_cli(stack_repo, ger_main, ["setup", "--help"], monkeypatch, catch_sys_exit=True)
    assert code == 0
    assert "ger setup" in out and "ger.webUrl" in out and "--token-file" in out


def test_without_a_terminal_it_asks_for_the_flags_and_saves_nothing(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    git("config", "--unset", "ger.webUrl", cwd=stack_repo, check=False)

    code, _out, err = _setup(stack_repo, ["--local"], monkeypatch)

    assert code == ExitCode.USAGE
    assert "no terminal to ask in: give --url, --user, --token-file" in err
    assert _config(stack_repo, "ger.user", "--local") is None


def test_ger_setup_treats_windows_nul_as_non_interactive(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr("gerrit_workflow_tools.cli_terminal._is_windows_console", lambda _stream: False)

    code, _out, err = run_cli(stack_repo, ger_main, ["setup"], monkeypatch)

    assert code == ExitCode.USAGE
    assert "no terminal to ask in" in err


def test_from_flags_it_checks_saves_installs_the_hook_and_says_what_next(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    token = tmp_path / "token"
    token.write_text("secret-token\n", encoding="utf-8")
    argv = ["--local", "--url", "https://gerrit.example.com/", "--user", "alice", "--token-file", str(token)]

    code, _out, err = _setup(stack_repo, argv, monkeypatch)

    assert code == 0, err
    assert _config(stack_repo, "ger.webUrl", "--local") == "https://gerrit.example.com"
    assert _config(stack_repo, "ger.token", "--local") == "secret-token"
    hook = _hook(stack_repo)
    assert hook.read_text(encoding="utf-8") == ChangeStore.commit_msg_hook and os.access(hook, os.X_OK)
    assert err.splitlines() == [
        "Signed in as Alice Reviewer <alice@example.com> on https://gerrit.example.com",
        "saved to your repository git config: ger.webUrl, ger.user and ger.token",
        f"installed Gerrit's commit-msg hook, so each new commit gets a Change-Id: {hook}",
        f"to take it out: rm {hook}",
        "next: `ger log` shows your stack and its reviews; `ger inbox` the changes waiting for you",
    ]


def test_rejected_credentials_save_nothing_and_exit_as_gerrit_failures(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("wrong-token\n"))
    argv = ["--local", "--url", "https://gerrit.example.com", "--user", "alice", "--token-file", "-"]

    code, _out, err = _setup(stack_repo, argv, monkeypatch, _Rejecting())

    assert code == ExitCode.GERRIT
    assert "Gerrit at gerrit.example.com did not accept user alice with that token; nothing was saved" in err
    assert _config(stack_repo, "ger.token", "--local") is None
    assert not _hook(stack_repo).exists()


def test_plain_http_needs_the_opt_in_said_up_front(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("tok\n"))
    argv = ["--local", "--url", "http://gerrit.test:8081", "--user", "alice", "--token-file", "-"]

    code, _out, err = _setup(stack_repo, argv, monkeypatch)
    assert code == ExitCode.USAGE
    assert "sends your user and token unencrypted" in err and "--allow-insecure-http" in err

    monkeypatch.setattr(sys, "stdin", io.StringIO("tok\n"))
    code, _out, err = _setup(stack_repo, [*argv, "--allow-insecure-http", "--no-hook"], monkeypatch)
    assert code == 0, err
    assert _config(stack_repo, "ger.allowInsecureHttp", "--local") == "true"
    assert "saved to your repository git config: ger.webUrl, ger.user, ger.allowInsecureHttp and ger.token" in err
    assert not _hook(stack_repo).exists()


def test_an_existing_hook_is_left_alone(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hook = _hook(stack_repo)
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\n# mine\n", encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", io.StringIO("tok\n"))
    argv = ["--local", "--url", "https://g.example", "--user", "alice", "--token-file", "-"]

    code, _out, err = _setup(stack_repo, argv, monkeypatch)

    assert code == 0, err
    assert hook.read_text(encoding="utf-8") == "#!/bin/sh\n# mine\n"
    assert "commit-msg" not in err


def test_in_a_terminal_a_wrong_answer_is_asked_again(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No scheme: asked again. Plain http: explained, allowed only on a yes. A 401: user and token asked again."""
    _answers(monkeypatch, ["gerrit.test:8081", "http://gerrit.test:8081", "alice", "wrong", "alice", "right"], ["y"])
    tries = iter([_Rejecting(), _store()])

    class _Flaky(ChangeStore):
        def get_account(self, account_id: int | str) -> dict[str, Any]:
            return next(tries).get_account(account_id)

    code, _out, err = _setup(stack_repo, ["--local", "--no-hook"], monkeypatch, _Flaky())

    assert code == 0, err
    assert "the web URL must start with https://" in err
    assert err.count("sends your user and token unencrypted") == 1  # not asked twice for the same URL
    assert "Gerrit at gerrit.test:8081 did not accept user alice with that token; nothing was saved" in err
    assert "Type the user and token again" in err
    assert "Signed in as Alice Reviewer <alice@example.com> on http://gerrit.test:8081" in err
    assert _config(stack_repo, "ger.token", "--local") == "right"
    assert _config(stack_repo, "ger.allowInsecureHttp", "--local") == "true"


def test_ger_setup_writes_global_config(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _answers(monkeypatch, ["https://gerrit.example.com", "alice", "secret-token"])

    code, _out, err = _setup(stack_repo, ["--no-hook"], monkeypatch)

    assert code == 0, err
    assert "saved to your global git config" in err
    assert _config(stack_repo, "ger.webUrl", "--global") == "https://gerrit.example.com"
    assert _config(stack_repo, "ger.user", "--global") == "alice"
    assert _config(stack_repo, "ger.token", "--global") == "secret-token"


def test_an_empty_answer_stops_and_saves_nothing(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _answers(monkeypatch, ["https://gerrit.example.com", ""])

    code, _out, err = _setup(stack_repo, ["--local"], monkeypatch)

    assert code == ExitCode.REFUSED
    assert "setup aborted: nothing was saved" in err
    assert _config(stack_repo, "ger.user", "--local") is None


def test_declining_plain_http_asks_for_the_url_again(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _answers(monkeypatch, ["http://gerrit.test", "https://gerrit.test", "alice", "tok"], ["n"])

    code, _out, err = _setup(stack_repo, ["--local", "--no-hook"], monkeypatch)

    assert code == 0, err
    assert _config(stack_repo, "ger.webUrl", "--local") == "https://gerrit.test"
    assert _config(stack_repo, "ger.allowInsecureHttp", "--local") is None
