"""A project-local ``registry.py`` runs only when trusted (issue #107).

The registry here records that it ran by writing a marker file when it is imported, so each test
checks whether the project's code ran at all, not only whether its strategy was used.
"""

from __future__ import annotations

import importlib.util
import marshal
import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_log import main as log_main
from gerrit_workflow_tools.core.config import ConfigScope, Settings
from gerrit_workflow_tools.core.ger_registry import (
    RegistryLoadError,
    clear_extension_registry_cache,
    resolve_registry_callable,
)
from gerrit_workflow_tools.core.git_run import git
from gerrit_workflow_tools.core.registry_trust import (
    read_registry_source,
    trust_store_path,
    untrusted_local_registries,
)
from tests.change_store import ChangeStore
from tests.cli_gerrit_mocks import build_details_by_change_id, stack_rows_mb_to_head
from tests.conftest import run_cli
from tests.fixtures import trust_local_registries


@pytest.fixture(autouse=True)
def _clear_registries() -> Iterator[None]:
    clear_extension_registry_cache()
    yield
    clear_extension_registry_cache()


def _plant_registry(repo: Path, marker: Path, domain: str = "ready") -> Path:
    """A ``.ger/<domain>/`` registry, as a change could add it, that writes *marker* when it runs."""
    d = repo / ".ger" / domain
    d.mkdir(parents=True, exist_ok=True)
    (d / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    (d / "registry.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n"
        "def _first(**_kwargs):\n    return None\nSTRATEGIES = {'p': _first}\n",
        encoding="utf-8",
    )
    return d


def _resolve(repo: Path, settings: Settings) -> object:
    return resolve_registry_callable(repo, "p", domain="ready", package_name="ger_ready", settings=settings)


def test_a_registry_in_the_work_tree_does_not_run_untrusted(stack_repo: Path, tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    _plant_registry(stack_repo, marker)

    assert _resolve(stack_repo, Settings.from_map({})) is None
    assert not marker.exists()


def test_scripts_dir_in_the_repositorys_own_config_trusts_it(stack_repo: Path, tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    _plant_registry(stack_repo, marker)
    git("config", "gerrit.scriptsDir", ".ger", cwd=stack_repo)

    assert _resolve(stack_repo, Settings.from_cwd(stack_repo)) is not None
    assert marker.exists()


@pytest.mark.parametrize("scope", [ConfigScope.GLOBAL, ConfigScope.SYSTEM])
def test_scripts_dir_from_global_config_trusts_only_a_directory_outside_the_work_tree(
    stack_repo: Path, tmp_path: Path, scope: ConfigScope
) -> None:
    marker = tmp_path / "ran"
    _plant_registry(stack_repo, marker)
    inside = Settings.from_map({"gerrit.scriptsDir": ".ger"}, scopes={"gerrit.scriptsDir": scope})
    assert _resolve(stack_repo, inside) is None
    assert not marker.exists()

    outside = tmp_path / "scripts"
    _plant_registry(outside, marker)
    settings = Settings.from_map({"gerrit.scriptsDir": str(outside / ".ger")}, scopes={"gerrit.scriptsDir": scope})
    assert _resolve(stack_repo, settings) is not None
    assert marker.exists()


def test_trusted_contents_run_and_a_changed_helper_needs_trust_again(stack_repo: Path, tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    d = _plant_registry(stack_repo, marker)
    trust_local_registries(stack_repo)
    assert untrusted_local_registries(stack_repo, Settings.from_map({})) == []
    assert _resolve(stack_repo, Settings.from_map({})) is not None
    assert marker.exists()

    marker.unlink()
    (d / "helper.py").write_text("VALUE = 2\n", encoding="utf-8")
    clear_extension_registry_cache()
    [changed] = untrusted_local_registries(stack_repo, Settings.from_map({}))
    assert changed.path == d / "registry.py"
    assert _resolve(stack_repo, Settings.from_map({})) is None
    assert not marker.exists()


def test_the_same_contents_are_trusted_in_another_worktree(stack_repo: Path, tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    _plant_registry(stack_repo, marker)
    trust_local_registries(stack_repo)
    other = tmp_path / "other"
    _plant_registry(other, marker)

    assert untrusted_local_registries(other, Settings.from_map({})) == []


# --- what runs is what was hashed ------------------------------------------------------------


def _payload(marker: Path) -> bytes:
    """Bytecode that writes *marker*: what a change could plant beside trusted source."""
    code = compile(f"from pathlib import Path\nPath({str(marker)!r}).write_text('payload')\n", "x.py", "exec")
    unchecked_hash_based = (0b01).to_bytes(4, "little")  # PEP 552: Python never compares it with the source.
    return importlib.util.MAGIC_NUMBER + unchecked_hash_based + b"\0" * 8 + marshal.dumps(code)


def test_planted_bytecode_does_not_run_in_place_of_trusted_source(stack_repo: Path, tmp_path: Path) -> None:
    marker, payload = tmp_path / "ran", tmp_path / "payload"
    d = _plant_registry(stack_repo, marker)
    registry = d / "registry.py"
    registry.write_text("from .helper import VALUE\n" + registry.read_text(encoding="utf-8"), encoding="utf-8")
    trust_local_registries(stack_repo)
    for source in (registry, d / "helper.py"):
        cached = Path(importlib.util.cache_from_source(str(source)))
        cached.parent.mkdir(exist_ok=True)
        cached.write_bytes(_payload(payload))

    assert untrusted_local_registries(stack_repo, Settings.from_map({})) == []
    assert _resolve(stack_repo, Settings.from_map({})) is not None
    assert marker.exists()
    assert not payload.exists()


def test_a_helper_with_only_bytecode_is_not_importable(stack_repo: Path, tmp_path: Path) -> None:
    payload = tmp_path / "payload"
    d = _plant_registry(stack_repo, tmp_path / "ran")
    (d / "registry.py").write_text("from . import ghost\n", encoding="utf-8")
    (d / "ghost.pyc").write_bytes(_payload(payload))
    trust_local_registries(stack_repo)

    with pytest.raises(RegistryLoadError):
        _resolve(stack_repo, Settings.from_map({}))
    assert not payload.exists()


def _symlink_dir(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"no directory symlinks here: {exc}")


def test_a_helper_reached_through_a_symlinked_directory_is_part_of_the_digest(stack_repo: Path, tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    d = _plant_registry(stack_repo, marker)
    shared = stack_repo / "shared"
    shared.mkdir()
    (shared / "strategy.py").write_text("VALUE = 1\n", encoding="utf-8")
    _symlink_dir(d / "helpers", shared)
    registry = d / "registry.py"
    registry.write_text("from .helpers.strategy import VALUE\n" + registry.read_text(encoding="utf-8"), "utf-8")
    trust_local_registries(stack_repo)
    assert _resolve(stack_repo, Settings.from_map({})) is not None

    marker.unlink()
    (shared / "strategy.py").write_text("VALUE = 2\n", encoding="utf-8")
    clear_extension_registry_cache()
    assert [r.path for r in untrusted_local_registries(stack_repo, Settings.from_map({}))] == [registry]
    assert _resolve(stack_repo, Settings.from_map({})) is None
    assert not marker.exists()


def test_a_symlink_cycle_is_read_once(tmp_path: Path) -> None:
    d = tmp_path / "ready"
    d.mkdir()
    (d / "registry.py").write_text("X = 1\n", encoding="utf-8")
    _symlink_dir(d / "loop", d)

    assert list(read_registry_source(d).files) == [("registry.py",)]


def test_an_unreadable_trust_store_trusts_nothing(stack_repo: Path, tmp_path: Path) -> None:
    _plant_registry(stack_repo, tmp_path / "ran")
    trust_store_path().parent.mkdir(parents=True)
    trust_store_path().write_text("not json", encoding="utf-8")

    assert len(untrusted_local_registries(stack_repo, Settings.from_map({}))) == 1


# --- the CLI asks before the command runs -----------------------------------------------------


def _log(repo: Path, monkeypatch: pytest.MonkeyPatch, *argv: str) -> tuple[int, str, str]:
    rows = stack_rows_mb_to_head(repo)
    store = ChangeStore(build_details_by_change_id(rows))
    return run_cli(repo, log_main, ["--color=never", *argv], monkeypatch, gerrit=store)


def test_ger_log_without_a_terminal_warns_and_does_not_run_it(
    web_stack_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "ran"
    d = _plant_registry(web_stack_repo, marker)
    monkeypatch.setattr("builtins.input", lambda *_a: pytest.fail("no terminal: ger must not ask"))

    code, _out, err = _log(web_stack_repo, monkeypatch)

    assert code in (0, 1), err
    assert f"not running untrusted {d / 'registry.py'}" in err
    assert "git config gerrit.scriptsDir" in err
    assert not marker.exists()


def test_ger_log_on_a_terminal_asks_and_a_yes_is_remembered(
    web_stack_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "ran"
    _plant_registry(web_stack_repo, marker)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    questions: list[str] = []

    def answer_yes(prompt: str = "") -> str:
        questions.append(prompt)
        return "y"

    monkeypatch.setattr("builtins.input", answer_yes)

    code, _out, err = _log(web_stack_repo, monkeypatch)
    assert code in (0, 1), err
    assert len(questions) == 1 and "Trust it" in questions[0]
    assert "Gerrit credentials" in err
    assert marker.exists()

    clear_extension_registry_cache()
    code, _out, err = _log(web_stack_repo, monkeypatch)
    assert code in (0, 1), err
    assert len(questions) == 1, "trusted contents are not asked about again"


def test_ger_log_on_a_terminal_declined_does_not_run_it(
    web_stack_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "ran"
    _plant_registry(web_stack_repo, marker)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "n")

    code, _out, err = _log(web_stack_repo, monkeypatch)

    assert code in (0, 1), err
    assert "not running untrusted" in err
    assert not marker.exists()
    assert not trust_store_path().exists()


def test_assume_yes_does_not_trust_project_code(
    web_stack_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "ran"
    _plant_registry(web_stack_repo, marker)
    monkeypatch.setenv("GER_ASSUME_YES", "1")

    code, _out, err = _log(web_stack_repo, monkeypatch)

    assert code in (0, 1), err
    assert "not running untrusted" in err
    assert not marker.exists()
