from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from tests.fixtures import (
    configure_gerrit_target,
    make_repo_duplicate_change_id,
    make_repo_malformed_cid,
    make_stack_repo,
)


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
) -> tuple[int, str, str]:
    """Run a CLI main with cwd set; capture stdout and stderr."""
    import io
    import sys

    monkeypatch.chdir(cwd)
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out_buf)
    monkeypatch.setattr(sys, "stderr", err_buf)
    code = main_fn(list(argv))
    return code, out_buf.getvalue(), err_buf.getvalue()


def json_stdout(stdout: str) -> dict:
    return json.loads(stdout)
