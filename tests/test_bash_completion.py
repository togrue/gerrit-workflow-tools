"""Tests for ``ger bash-completion``."""

from __future__ import annotations

import functools
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from gerrit_workflow_tools import cli_bash_completion as bc
from gerrit_workflow_tools.bash_completion_generator import _parser_builders, render_bash_completion_script
from gerrit_workflow_tools.cli_ger import _COMMANDS, main as ger_main
from tests.conftest import run_cli


@functools.cache
def _bash() -> str | None:
    """A bash that runs, or ``None``.

    On Windows ``bash`` on the PATH is often WSL's launcher, which fails without an installed
    distribution (a CI runner); Git for Windows ships its own next to ``git.exe``, so prefer that.
    """
    candidates: list[str] = []
    git = shutil.which("git")
    if sys.platform == "win32" and git:
        root = Path(git).resolve().parent.parent  # <Git>/cmd/git.exe or <Git>/bin/git.exe
        candidates += [str(root / "bin" / "bash.exe"), str(root / "usr" / "bin" / "bash.exe")]
    if found := shutil.which("bash"):
        candidates.append(found)
    for bash in candidates:
        try:
            probe = subprocess.run([bash, "-c", "echo ok"], capture_output=True, text=True, check=False, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if probe.returncode == 0 and probe.stdout.strip() == "ok":
            return bash
    return None


def _require_bash() -> str:
    bash = _bash()
    if bash is None:
        pytest.skip("no working bash")
    return bash


def test_strip_marked_blocks_removes_block() -> None:
    text = f'echo before\n{bc.MARKER_START}\nsource "/x/ger.bash"\n{bc.MARKER_END}\necho after\n'
    assert "before" in bc._strip_marked_blocks(text)
    assert "after" in bc._strip_marked_blocks(text)
    assert bc.MARKER_START not in bc._strip_marked_blocks(text)


def test_source_command_line_looks_like_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    line = bc.source_command_line()
    assert line.startswith('source "')
    assert line.endswith('ger.bash"')


def test_main_prints_source_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    code, out, err = run_cli(tmp_path, bc.main, [], monkeypatch)
    assert code == 0
    assert out.strip().startswith("source ")
    assert err == ""


def test_printed_line_is_valid_bash_that_runs_nothing_but_echo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _code, out, _err = run_cli(tmp_path, bc.main, [], monkeypatch)
    assert "`" not in out
    assert subprocess.run([_require_bash(), "-n", "-c", out], check=False).returncode == 0


def test_install_and_uninstall_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rc = tmp_path / "bashrc"
    monkeypatch.chdir(tmp_path)
    code, _out, err = run_cli(tmp_path, bc.main, ["--install", "--rc-file", str(rc)], monkeypatch)
    assert code == 0
    assert bc.MARKER_START in err
    assert "Using completion script" in err
    body = rc.read_text(encoding="utf-8")
    assert bc.MARKER_START in body
    assert bc.MARKER_END in body

    code2, _out2, err2 = run_cli(tmp_path, bc.main, ["--uninstall", "--rc-file", str(rc)], monkeypatch)
    assert code2 == 0
    assert "Removing marked" in err2
    assert bc.MARKER_START not in rc.read_text(encoding="utf-8")


def test_install_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rc = tmp_path / "bashrc"
    monkeypatch.chdir(tmp_path)
    run_cli(tmp_path, bc.main, ["--install", "--rc-file", str(rc)], monkeypatch)
    run_cli(tmp_path, bc.main, ["--install", "--rc-file", str(rc)], monkeypatch)
    assert rc.read_text(encoding="utf-8").count(bc.MARKER_START) == 1


def test_uninstall_no_block_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rc = tmp_path / "bashrc"
    rc.write_text("# empty\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    code, _out, err = run_cli(tmp_path, bc.main, ["--uninstall", "--rc-file", str(rc)], monkeypatch)
    assert code == 1
    assert "no gerrit-workflow-tools completion block" in err


def test_uninstall_missing_file_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = tmp_path / "nope"
    monkeypatch.chdir(tmp_path)
    code, _out, err = run_cli(tmp_path, bc.main, ["--uninstall", "--rc-file", str(missing)], monkeypatch)
    assert code == 1
    assert "does not exist" in err


def test_ger_dispatches_bash_completion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    code, out, err = run_cli(tmp_path, ger_main, ["bash-completion"], monkeypatch)
    assert code == 0
    assert out.strip().startswith("source ")
    assert err == ""


def test_regenerated_bash_completion_matches_checked_in_file() -> None:
    completion_path = Path(__file__).resolve().parents[1] / "contrib" / "completion" / "ger.bash"
    assert completion_path.read_text(encoding="utf-8") == render_bash_completion_script()


def test_every_ger_command_has_a_completion_function() -> None:
    """A command missing here dispatches to an undefined _ger_<cmd> and tab prints "command not found"."""
    assert sorted(_parser_builders()) == sorted(_COMMANDS)


def _complete(words: list[str]) -> subprocess.CompletedProcess[str]:
    # Relative path and script on stdin: on Windows `bash` may be WSL's, which cannot read D:/
    # paths and mangles a -c script.
    repo_root = Path(__file__).resolve().parents[1]
    script = (
        "source contrib/completion/ger.bash; "
        f'COMP_WORDS=({" ".join(words)}); COMP_CWORD={len(words) - 1}; _ger; echo "${{COMPREPLY[*]}}"'
    )
    return subprocess.run([_require_bash()], input=script, capture_output=True, text=True, check=False, cwd=repo_root)


@pytest.mark.parametrize("command", sorted(_COMMANDS))
def test_flag_completion_runs_for_every_command(command: str) -> None:
    result = _complete(["ger", command, "--"])
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert "--help" in result.stdout.split()


def test_vscode_completes_its_action() -> None:
    assert _complete(["ger", "vscode", "''"]).stdout.split() == ["install"]
