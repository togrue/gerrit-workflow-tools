from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from tests.fixtures import (
    configure_gerrit_target,
    make_gcid_cli_repo,
    make_repo_duplicate_change_id,
    make_repo_malformed_cid,
    make_stack_repo,
)


def pytest_configure(config: pytest.Config) -> None:
    """Point Git at replacement global/system config so tests ignore the real ``~/.gitconfig``.

    The stub global file supplies only ``user.*`` so ``git commit`` works without per-call
    ``GIT_AUTHOR_*`` env. Production uses full ``git config --list`` (standard precedence);
    without this, tests would pick up the developer's ``gerrit.*`` keys and become flaky.
    """
    if os.environ.get("GERRIT_WORKFLOW_TOOLS_NO_GIT_CONFIG_ISOLATION", "").lower() in (
        "1",
        "true",
        "yes",
    ):
        return
    base = Path(tempfile.gettempdir()) / "gerrit-workflow-tools-test-gitconfig"
    base.mkdir(parents=True, exist_ok=True)
    stub_global = base / "stub-global"
    stub_system = base / "stub-system"
    stub_global.write_text(
        "[user]\n\tname = Test\n\temail = test@example.com\n",
        encoding="utf-8",
    )
    stub_system.write_text("", encoding="utf-8")
    os.environ.setdefault("GIT_CONFIG_GLOBAL", str(stub_global))
    os.environ.setdefault("GIT_CONFIG_SYSTEM", str(stub_system))


def _copy_git_repo(template: Path, dest: Path) -> Path:
    """Clone a session-built template into an isolated per-test directory."""
    shutil.copytree(template, dest)
    return dest


@pytest.fixture(scope="session")
def _stack_repo_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("tpl_stack")
    make_stack_repo(root / "repo")
    return root / "repo"


@pytest.fixture(scope="session")
def _dup_repo_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("tpl_dup")
    make_repo_duplicate_change_id(root / "repo")
    return root / "repo"


@pytest.fixture(scope="session")
def _malformed_cid_repo_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("tpl_malformed")
    make_repo_malformed_cid(root / "repo")
    return root / "repo"


@pytest.fixture(scope="session")
def _gcid_cli_repo_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("tpl_gcid_cli")
    make_gcid_cli_repo(root / "repo")
    return root / "repo"


@pytest.fixture
def gcid_cli_repo(tmp_path: Path, _gcid_cli_repo_template: Path) -> Path:
    """Isolated copy of a small repo with three predictable Change-Ids (see ``GCID_CLI_CHANGE_IDS``)."""
    return _copy_git_repo(_gcid_cli_repo_template, tmp_path / "gcid_cli")


@pytest.fixture
def stack_repo_unconfigured(tmp_path: Path, _stack_repo_template: Path) -> Path:
    """Same graph as make_stack_repo; no branch gerrit config."""
    return _copy_git_repo(_stack_repo_template, tmp_path / "r")


@pytest.fixture
def stack_repo(tmp_path: Path, _stack_repo_template: Path) -> Path:
    """Linear feature branch over main; third commit matches ^test!; gerritTarget=main."""
    repo = _copy_git_repo(_stack_repo_template, tmp_path / "stack")
    configure_gerrit_target(repo, "main")
    return repo


@pytest.fixture(autouse=True)
def _isolate_xdg_cache_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep Gerrit API cache rows from leaking between tests that share mock hosts."""

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))


@pytest.fixture
def dup_repo(tmp_path: Path, _dup_repo_template: Path) -> Path:
    repo = _copy_git_repo(_dup_repo_template, tmp_path / "dup")
    configure_gerrit_target(repo, "main")
    return repo


@pytest.fixture
def malformed_cid_repo(tmp_path: Path, _malformed_cid_repo_template: Path) -> Path:
    repo = _copy_git_repo(_malformed_cid_repo_template, tmp_path / "mal")
    configure_gerrit_target(repo, "main")
    return repo


def run_cli(
    cwd: Path,
    main_fn: Callable[[list[str] | None], int],
    argv: Sequence[str],
    monkeypatch: pytest.MonkeyPatch,
    *,
    catch_sys_exit: bool = False,
) -> tuple[int, str, str]:
    """Run a CLI main with cwd set; capture stdout and stderr.

    If *catch_sys_exit* is true, ``SystemExit`` (e.g. from ``--help``) is
    turned into a return code instead of propagating.
    """
    import io
    import sys

    monkeypatch.chdir(cwd)
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out_buf)
    monkeypatch.setattr(sys, "stderr", err_buf)
    try:
        code = main_fn(list(argv))
    except SystemExit as e:
        if not catch_sys_exit:
            raise
        if isinstance(e.code, int):
            code = e.code
        elif e.code is None:
            code = 0
        else:
            try:
                code = int(e.code)
            except (TypeError, ValueError):
                code = 1
    return code, out_buf.getvalue(), err_buf.getvalue()


def json_stdout(stdout: str) -> dict:
    return json.loads(stdout)
