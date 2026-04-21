"""Tests for ``ger bash-completion``."""

from __future__ import annotations

from pathlib import Path

import pytest

from gerrit_workflow_tools import cli_bash_completion as bc
from gerrit_workflow_tools.cli_ger import main as ger_main
from tests.conftest import run_cli


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
