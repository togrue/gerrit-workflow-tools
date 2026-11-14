"""The run context: one per command run, carrying its Settings, its Gerrit service and ``--refresh``."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_common import REFRESH_ENV, init_cli_runtime
from gerrit_workflow_tools.core.run_context import RunContext
from tests.change_store import ChangeStore


def test_a_command_run_shares_one_settings_snapshot_and_service(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(stack_repo)
    monkeypatch.delenv(REFRESH_ENV, raising=False)
    store = ChangeStore()

    run, _ = init_cli_runtime(argparse.Namespace(), gerrit=store)

    assert run.cwd.samefile(stack_repo)
    assert run.gerrit.settings is run.settings
    assert run.gerrit.rest is store
    assert run.gerrit.refresh is False


def test_ger_refresh_reaches_the_run_through_the_entry_point(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(stack_repo)
    monkeypatch.setenv(REFRESH_ENV, "1")

    run, _ = init_cli_runtime(argparse.Namespace(), gerrit=ChangeStore())

    assert run.gerrit.refresh is True


def test_opening_a_run_asks_nothing_of_gerrit(tmp_path: Path) -> None:
    """A run outside any Gerrit configuration opens; only asking Gerrit would need gerrit.webUrl."""
    run = RunContext.open(tmp_path)

    assert run.gerrit.configured is False
    assert run.settings is run.gerrit.settings
