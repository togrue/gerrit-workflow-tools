"""Tests for the unified ``ger`` CLI dispatcher."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_ger import main as ger_main
from tests.conftest import run_cli


def test_ger_no_args_prints_usage(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, _err = _run_ger(stack_repo, [], monkeypatch)
    assert code == 2
    assert "ger <command>" in out
    assert "log" in out


def test_ger_help(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, _err = _run_ger(stack_repo, ["--help"], monkeypatch)
    assert code == 0
    assert "ger <command>" in out


@pytest.mark.parametrize(
    ("command", "line"),
    [
        ("lgo", "error: unknown command 'lgo'; did you mean 'log'?"),
        ("nope", "error: unknown command 'nope'"),
    ],
)
def test_unknown_command_suggests_the_close_one(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch, command: str, line: str
) -> None:
    """Only a close match is suggested; the whole error line is pinned."""
    code, _out, err = _run_ger(stack_repo, [command], monkeypatch)
    assert code == 2
    assert line in err.splitlines()


def test_ger_log_help_delegates(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, _err = _run_ger(stack_repo, ["log", "--help"], monkeypatch, catch_sys_exit=True)
    assert code == 0
    assert "ger log" in out


def test_ger_inbox_help_delegates(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, _err = _run_ger(stack_repo, ["inbox", "--help"], monkeypatch, catch_sys_exit=True)
    assert code == 0
    assert "ger inbox" in out


def test_ger_rebase_help_delegates(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, _err = _run_ger(stack_repo, ["rebase", "--help"], monkeypatch, catch_sys_exit=True)
    assert code == 0
    assert "ger rebase" in out


def test_ger_restack_alias_delegates(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, _err = _run_ger(stack_repo, ["restack", "--help"], monkeypatch, catch_sys_exit=True)
    assert code == 0
    assert "ger rebase" in out


def test_ger_stack_alias_delegates(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, _err = _run_ger(stack_repo, ["stack", "--help"], monkeypatch, catch_sys_exit=True)
    assert code == 0
    assert "ger rebase" in out


def test_ger_changeid_alias_delegates(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, _err = _run_ger(stack_repo, ["changeid", "--help"], monkeypatch, catch_sys_exit=True)
    assert code == 0
    assert "ger change-id" in out


def test_ger_reword_help_delegates(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, _err = _run_ger(stack_repo, ["reword", "--help"], monkeypatch, catch_sys_exit=True)
    assert code == 0
    assert "ger reword" in out


def _run_ger(
    cwd: Path,
    argv: list[str],
    monkeypatch: pytest.MonkeyPatch,
    *,
    catch_sys_exit: bool = False,
) -> tuple[int, str, str]:
    return run_cli(cwd, ger_main, argv, monkeypatch, catch_sys_exit=catch_sys_exit)


@pytest.mark.parametrize(("word", "command"), [("status", "log"), ("list", "inbox"), ("comments", "show")])
def test_words_agents_try_point_to_the_command(
    word: str, command: str, stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, _out, err = run_cli(stack_repo, ger_main, [word], monkeypatch)
    assert code == 2
    assert f"did you mean {command!r}?" in err


def test_help_maps_common_tasks_to_commands(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every command the task index names is a real command (the index cannot drift from the registry)."""
    from gerrit_workflow_tools.cli_ger import _COMMANDS, _TASKS

    code, out, _err = run_cli(stack_repo, ger_main, ["--help"], monkeypatch)
    assert code == 0 and "common tasks:" in out and "GER_ASSUME_YES" in out
    named = {m for _task, how in _TASKS for m in re.findall(r"\bger ([a-z-]+)", how)}
    assert named and named <= set(_COMMANDS), named - set(_COMMANDS)


def test_every_command_is_in_exactly_one_help_section() -> None:
    """A new command cannot be left out of `ger --help`, nor listed twice."""
    from gerrit_workflow_tools.cli_ger import _COMMANDS, _SECTIONS

    listed = [name for _title, groups in _SECTIONS for group in groups for name in group]
    assert sorted(listed) == sorted(_COMMANDS)


def test_help_lists_daily_commands_before_setup_and_scripting(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, out, _err = run_cli(stack_repo, ger_main, ["--help"], monkeypatch)
    assert code == 0
    lines = out.splitlines()
    headers = [
        line for line in lines if line in {"daily commands:", "setup:", "scripting and agents:", "common tasks:"}
    ]
    assert headers == ["daily commands:", "setup:", "scripting and agents:", "common tasks:"]
    daily = lines[lines.index("daily commands:") + 1 : lines.index("setup:") - 1]
    assert [line.split()[0].rstrip(",") for line in daily][:5] == ["log", "show", "edit", "fix", "inbox"]
    assert any(line.startswith("  edit, reword ") for line in daily)
    scripting = lines[lines.index("scripting and agents:") + 1 : lines.index("common tasks:") - 1]
    assert "  sha " in "\n".join(scripting)
