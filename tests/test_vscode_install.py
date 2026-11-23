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


@pytest.fixture
def checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A ger installed from a checkout: nothing bundled, the extension's sources beside it."""
    sources = tmp_path / "clone" / "vscode"
    sources.mkdir(parents=True)
    (sources / "package.json").write_text("{}", encoding="utf-8")
    (sources.parent / "LICENSE").write_text("licence", encoding="utf-8")
    monkeypatch.setattr("gerrit_workflow_tools.cli_vscode.bundled_extension", lambda: None)
    monkeypatch.setattr("gerrit_workflow_tools.cli_vscode.source_extension", lambda: sources)
    return sources


@pytest.fixture
def npm_runs(editor_runs: list[list[str]], monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """npm is on PATH too, recorded apart from the editor; ``package`` writes the ``--out`` file."""
    runs: list[list[str]] = []
    monkeypatch.setattr(
        editor_extension.shutil, "which", lambda name: f"/bin/{name}" if name in ("code", "cursor", "npm") else None
    )

    def record(argv: list[str], cwd: Path | None = None) -> int:
        if argv[0] != "/bin/npm":
            editor_runs.append(argv)
            return 0
        assert cwd is not None
        runs.append(argv[1:])
        if argv[1] == "run":
            assert (cwd / "LICENSE").read_text(encoding="utf-8") == "licence", "the package must carry the licence"
        if "package" in argv:
            Path(argv[-1]).write_bytes(b"vsix")
        return 0

    monkeypatch.setattr(editor_extension, "run_editor_cli", record)
    return runs


def test_this_checkout_is_where_a_source_install_finds_the_extension() -> None:
    """Tests run from a source checkout: its ``vscode/`` folder is found, nothing is bundled (#35)."""
    assert editor_extension.bundled_extension() is None
    sources = editor_extension.source_extension()
    assert sources is not None
    assert sources == Path(__file__).resolve().parents[1] / "vscode"


def test_an_install_from_a_checkout_builds_the_extension_and_installs_it(
    tmp_path: Path,
    checkout: Path,
    npm_runs: list[list[str]],
    editor_runs: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    code, _out, err = run_cli(tmp_path, ger_main, ["vscode", "install"], monkeypatch)
    assert (code, err) == (0, "")
    assert [run[:2] for run in npm_runs] == [["ci", "--no-audit"], ["run", "compile"], ["run", "package"]]
    built = Path(npm_runs[-1][-1])
    assert built.name == editor_extension.EXTENSION_FILE
    assert editor_runs == [["/bin/code", "--install-extension", str(built), "--force"]]
    assert not (checkout / "LICENSE").exists(), "the licence copied in for the build is removed again"
    assert not built.exists(), "the built package is not left behind"


def test_installed_dependencies_are_not_reinstalled(
    tmp_path: Path, checkout: Path, npm_runs: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    (checkout / "node_modules").mkdir()
    code, _out, _err = run_cli(tmp_path, ger_main, ["vscode", "install"], monkeypatch)
    assert code == 0
    assert [run[:2] for run in npm_runs] == [["run", "compile"], ["run", "package"]]


def test_a_failed_build_installs_nothing_and_is_the_commands_exit_code(
    tmp_path: Path, checkout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(editor_extension.shutil, "which", lambda name: f"/bin/{name}")
    runs: list[list[str]] = []

    def fail_compile(argv: list[str], cwd: Path | None = None) -> int:
        runs.append(argv)
        return 3 if "compile" in argv else 0

    monkeypatch.setattr(editor_extension, "run_editor_cli", fail_compile)
    code, _out, _err = run_cli(tmp_path, ger_main, ["vscode", "install"], monkeypatch)
    assert code == 3
    assert not any("--install-extension" in argv for argv in runs)
    assert not (checkout / "LICENSE").exists()


def test_building_without_npm_says_so(
    tmp_path: Path, checkout: Path, editor_runs: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    code, _out, err = run_cli(tmp_path, ger_main, ["vscode", "install"], monkeypatch)
    assert code == 2
    assert editor_runs == []
    assert f"building the VS Code extension from {checkout} needs npm on PATH" in err


def test_a_missing_editor_is_named_before_the_build(
    tmp_path: Path, checkout: Path, npm_runs: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    code, _out, err = run_cli(tmp_path, ger_main, ["vscode", "install", "--editor", "nosuch"], monkeypatch)
    assert code == 2
    assert npm_runs == []
    assert "editor command 'nosuch' not found" in err


def test_without_a_bundle_or_a_checkout_it_names_both_ways_to_get_one(
    tmp_path: Path, editor_runs: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("gerrit_workflow_tools.cli_vscode.bundled_extension", lambda: None)
    monkeypatch.setattr("gerrit_workflow_tools.cli_vscode.source_extension", lambda: None)
    code, _out, err = run_cli(tmp_path, ger_main, ["vscode", "install"], monkeypatch)
    assert code == 2
    assert editor_runs == []
    assert f"{editor_extension.REPOSITORY_URL}/releases)" in err
    assert "uv tool install -e <clone>" in err
    # No address of one release asset: it does not exist until that version is released (#35).
    assert "/releases/download/" not in err
    assert "/releases/latest/download/" not in err


def test_ger_and_the_extension_state_one_version() -> None:
    """A release is one version of both: the extension installs the ger release with its own version."""
    stated = versions()
    assert len(set(stated.values())) == 1, stated
    assert __version__ in stated.values()
