"""When Gerrit cannot be asked at all, every command says so once and never guesses.

Pins agreement *between* commands: ``ger log``, ``ger show``, ``ger edit -n`` and the push
preview meet an unreachable Gerrit (or one that rejects the credentials) through the same
seam, report the same one line, and show local state as unknown rather than "not pushed".
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError

import pytest

from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_edit import main as edit_main
from gerrit_workflow_tools.cli_log import main as log_main
from gerrit_workflow_tools.cli_push import main as push_main
from gerrit_workflow_tools.cli_show import main as show_main
from gerrit_workflow_tools.core.gerrit import http_rest
from gerrit_workflow_tools.core.gerrit.credentials import GerritAuth
from gerrit_workflow_tools.core.gerrit.http_rest import HttpGerritRest
from gerrit_workflow_tools.core.gerrit.rest import GerritUnavailableError
from gerrit_workflow_tools.core.git_run import git
from tests.conftest import json_stdout, run_cli


UNREACHABLE = "Gerrit unreachable at g.example (connection refused)"


class _DownGerrit:
    """A ``GerritRest`` whose every call fails the way an unreachable host does."""

    web_base = "https://g.example"

    def __init__(self, message: str = UNREACHABLE) -> None:
        self.message = message
        self.calls = 0

    def __getattr__(self, name: str) -> Callable[..., Any]:
        def _fail(*_args: object, **_kwargs: object) -> Any:
            self.calls += 1
            raise GerritUnavailableError(self.message)

        return _fail


def _configure(repo: Path) -> None:
    git("config", "ger.webUrl", "https://g.example", cwd=repo)


@pytest.mark.parametrize(("main_fn", "argv"), [(log_main, []), (show_main, ["HEAD"])])
def test_overlay_commands_show_local_state_and_report_gerrit_once(
    main_fn: Callable[..., int], argv: list[str], stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(stack_repo)
    code, out, err = run_cli(stack_repo, main_fn, argv, monkeypatch, gerrit=_DownGerrit())

    assert code == ExitCode.GERRIT
    assert err.strip().splitlines() == [f"gerrit error: {UNREACHABLE}"]
    assert "not-pushed" not in out
    assert "Gerrit state unknown" in out


def test_log_asks_a_down_gerrit_once_not_per_change(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(stack_repo)
    down = _DownGerrit()
    run_cli(stack_repo, log_main, [], monkeypatch, gerrit=down)
    assert down.calls == 1


def test_log_json_marks_every_change_unknown(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(stack_repo)
    code, out, _err = run_cli(stack_repo, log_main, ["--json"], monkeypatch, gerrit=_DownGerrit())

    assert code == ExitCode.GERRIT
    statuses = {c["patchset_status"] for c in json_stdout(out)["commits"] if c["change_id"]}
    assert statuses == {"unknown"}


def test_edit_walk_refuses_to_call_unknown_state_nothing_to_do(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(stack_repo)
    code, _out, err = run_cli(stack_repo, edit_main, ["-n"], monkeypatch, gerrit=_DownGerrit())

    assert code == ExitCode.GERRIT
    assert err.strip().splitlines() == [f"gerrit error: {UNREACHABLE}"]


def test_push_preview_warns_once_and_still_lists_the_commits(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(stack_repo)
    code, out, err = run_cli(stack_repo, push_main, ["--dry-run"], monkeypatch, gerrit=_DownGerrit())

    assert code == ExitCode.OK
    assert err.count(UNREACHABLE) == 1
    assert "About to push" in out + err


def test_show_change_number_with_rejected_credentials_is_not_not_found(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(stack_repo)
    rejected = "Gerrit at g.example rejected the credentials (HTTP 401)"
    code, _out, err = run_cli(stack_repo, show_main, ["change:801"], monkeypatch, gerrit=_DownGerrit(rejected))

    assert code == ExitCode.GERRIT
    assert "not found" not in err
    assert rejected in err


@pytest.mark.parametrize(
    ("raised", "message"),
    [
        (
            URLError(ConnectionRefusedError(111, "localized prose")),
            "Gerrit unreachable at g.example:8081 (connection refused)",
        ),
        (
            HTTPError("https://g.example:8081/a/changes/", 401, "Unauthorized", {}, None),  # type: ignore[arg-type]
            "Gerrit at g.example:8081 rejected the credentials (HTTP 401); check ger.https://g.example:8081.user "
            "and ger.https://g.example:8081.token, or run `ger setup`",
        ),
    ],
)
def test_http_rest_names_a_down_or_refusing_gerrit(
    raised: Exception, message: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _urlopen(*_args: object, **_kwargs: object) -> Any:
        raise raised

    monkeypatch.setattr(http_rest, "urlopen", _urlopen)
    rest = HttpGerritRest("https://g.example:8081", auth=GerritAuth("u", "s"))
    with pytest.raises(GerritUnavailableError) as caught:
        rest.query_changes("status:open")
    assert str(caught.value) == message


def test_unresolvable_revision_reads_the_same_in_every_command(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bare number is a git rev first, then a change number; every command says so the same way."""
    from gerrit_workflow_tools.cli_edit import main_reword
    from gerrit_workflow_tools.cli_fix import main as fix_main
    from gerrit_workflow_tools.cli_rebase import main as rebase_main
    from gerrit_workflow_tools.cli_resolve import main as resolve_main
    from tests.change_store import ChangeStore

    _configure(stack_repo)
    expected = "error: cannot resolve '801': no commit by that name, and Gerrit has no such change"
    for main_fn, argv in (
        (show_main, ["801"]),
        (edit_main, ["801"]),
        (main_reword, ["801"]),
        (fix_main, ["801"]),
        (rebase_main, ["801"]),
        (resolve_main, ["801"]),
        (push_main, ["--dry-run", "801"]),
    ):
        code, _out, err = run_cli(stack_repo, main_fn, argv, monkeypatch, gerrit=ChangeStore({}))
        assert (code, err.strip()) == (ExitCode.NOT_FOUND, expected), main_fn.__module__
    # A revision range is git's alone: no change to fall back to, so the hint stays.
    code, _out, err = run_cli(stack_repo, log_main, ["801"], monkeypatch)
    hint = "error: cannot resolve '801': no commit by that name; did you mean change:801?"
    assert (code, err.strip()) == (ExitCode.NOT_FOUND, hint)


def test_a_change_id_lookalike_says_what_a_change_id_is(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(stack_repo)
    code, _out, err = run_cli(stack_repo, show_main, ["Inotachangeid"], monkeypatch)
    assert code == ExitCode.NOT_FOUND
    assert "no Change-Id either (I plus 40 hex digits)" in err


def test_a_host_that_does_not_answer_is_said_so_with_the_wait_and_not_asked_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wait that runs out names how long it waited; the rest of the run does not wait again (ADR-0014)."""
    calls: list[str] = []

    def _silent(request: Any, **_kwargs: object) -> Any:
        calls.append(request.full_url)
        raise URLError(TimeoutError("timed out"))

    monkeypatch.setattr(http_rest, "urlopen", _silent)
    rest = HttpGerritRest("https://g.example:8081", auth=GerritAuth("u", "s"))
    for _ in range(3):
        with pytest.raises(GerritUnavailableError) as caught:
            rest.query_changes("status:open")
        assert str(caught.value) == "Gerrit at g.example:8081 does not answer (waited 0 s)"
        assert caught.value.reason == "no-answer"
    assert len(calls) == 1
    # The next run builds its own client, and asks again.
    with pytest.raises(GerritUnavailableError):
        HttpGerritRest("https://g.example:8081", auth=GerritAuth("u", "s")).query_changes("status:open")
    assert len(calls) == 2


def test_connecting_waits_at_most_the_connect_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """A request's long read timeout does not make connecting to a silent host wait as long."""
    from gerrit_workflow_tools.core import http_connect

    limits: list[float | None] = []

    def _race(_infos: object, limit: float | None, _source: object) -> Any:
        limits.append(limit)
        raise TimeoutError("timed out")

    monkeypatch.setattr(http_connect, "_race", _race)
    resolve = lambda *_a: [(2, 1, 6, "", ("10.255.255.1", 8081))]  # noqa: E731
    with pytest.raises(TimeoutError):
        http_connect.connect(("silent.example", 8081), 120, resolve=resolve)
    assert limits == [http_connect.CONNECT_TIMEOUT]
