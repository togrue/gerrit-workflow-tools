from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from tests.fixtures import (
    configure_gerrit_target,
    make_repo_duplicate_change_id,
    make_repo_malformed_cid,
    make_stack_repo,
)


@pytest.fixture
def stack_repo(tmp_path: Path) -> Path:
    """Linear feature branch over main; third commit matches ^test!; gerritTarget=main."""
    repo = make_stack_repo(tmp_path / "stack")
    configure_gerrit_target(repo, "main")
    return repo


@pytest.fixture
def dup_repo(tmp_path: Path) -> Path:
    repo = make_repo_duplicate_change_id(tmp_path / "dup")
    configure_gerrit_target(repo, "main")
    return repo


@pytest.fixture
def malformed_cid_repo(tmp_path: Path) -> Path:
    repo = make_repo_malformed_cid(tmp_path / "mal")
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
