"""``ger vscode install`` and what a release is built from."""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts.build_release import versions

from gerrit_workflow_tools import __version__
from gerrit_workflow_tools.cli_ger import main as ger_main
from gerrit_workflow_tools.core import editor_extension
from tests.conftest import run_cli


@pytest.fixture
def bundled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A ger that was released with its extension."""
    extension = tmp_path / editor_extension.EXTENSION_FILE
    extension.write_bytes(b"vsix")
    monkeypatch.setattr("gerrit_workflow_tools.cli_vscode.bundled_extension", lambda: extension)
    return extension


@pytest.fixture
def editor_runs(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Editors on PATH are ``code`` and ``cursor``; what they are run with is recorded."""
    runs: list[list[str]] = []
    monkeypatch.setattr(
        editor_extension.shutil, "which", lambda name: f"/bin/{name}" if name in ("code", "cursor") else None
    )

    def record(argv: list[str]) -> int:
        runs.append(argv)
        return 0

    monkeypatch.setattr(editor_extension, "run_editor_cli", record)
    return runs


def test_installs_the_bundled_extension_into_the_first_editor_found(
    tmp_path: Path, bundled: Path, editor_runs: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Outside any repository: installing is not about a clone.
    code, _out, err = run_cli(tmp_path, ger_main, ["vscode", "install"], monkeypatch)
    assert (code, err) == (0, "")
    assert editor_runs == [["/bin/code", "--install-extension", str(bundled), "--force"]]


def test_editor_option_names_another_editor(
    tmp_path: Path, bundled: Path, editor_runs: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    code, _out, _err = run_cli(tmp_path, ger_main, ["vscode", "install", "--editor", "cursor"], monkeypatch)
    assert code == 0
    assert editor_runs[0][0] == "/bin/cursor"


def test_the_editors_exit_code_is_the_commands(tmp_path: Path, bundled: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(editor_extension.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(editor_extension, "run_editor_cli", lambda argv: 1)
    code, _out, _err = run_cli(tmp_path, ger_main, ["vscode", "install"], monkeypatch)
    assert code == 1


def test_without_an_editor_command_it_says_where_the_extension_is(
    tmp_path: Path, bundled: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(editor_extension.shutil, "which", lambda name: None)
    code, _out, err = run_cli(tmp_path, ger_main, ["vscode", "install"], monkeypatch)
    assert code == 2
    assert "no editor command found on PATH" in err
    assert str(bundled) in err
    assert "Install from VSIX" in err

    code, _out, err = run_cli(tmp_path, ger_main, ["vscode", "install", "--editor", "nosuch"], monkeypatch)
    assert code == 2
    assert "editor command 'nosuch' not found" in err


def test_an_install_from_source_has_no_extension_and_names_the_release(
    tmp_path: Path, editor_runs: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("gerrit_workflow_tools.cli_vscode.bundled_extension", lambda: None)
    code, _out, err = run_cli(tmp_path, ger_main, ["vscode", "install"], monkeypatch)
    assert code == 2
    assert editor_runs == []
    assert "installed from source" in err
    assert "/releases/latest/download/ger.tar.gz" in err
    assert f"/releases/download/v{__version__}/ger-vscode.vsix" in err


def test_ger_and_the_extension_state_one_version() -> None:
    """A release is one version of both: the extension installs the ger release with its own version."""
    stated = versions()
    assert len(set(stated.values())) == 1, stated
    assert __version__ in stated.values()
