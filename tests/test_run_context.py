"""The run context: one per command run, carrying its Settings, its Gerrit service and ``--refresh``."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_common import REFRESH_ENV, init_cli_runtime
from gerrit_workflow_tools.core.git_run import git
from gerrit_workflow_tools.core.git_state import checked_out_branch_name
from gerrit_workflow_tools.core.run_context import RunContext
from tests.change_store import ChangeStore


def test_a_command_run_shares_one_settings_snapshot_and_service(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(stack_repo)
    monkeypatch.delenv(REFRESH_ENV, raising=False)
    store = ChangeStore()

    run = init_cli_runtime(argparse.Namespace(), gerrit=store)

    assert run.cwd.samefile(stack_repo)
    assert run.gerrit.settings is run.settings
    assert run.gerrit.rest is store
    assert run.gerrit.refresh is False


def test_ger_refresh_reaches_the_run_through_the_entry_point(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(stack_repo)
    monkeypatch.setenv(REFRESH_ENV, "1")

    run = init_cli_runtime(argparse.Namespace(), gerrit=ChangeStore())

    assert run.gerrit.refresh is True


def test_opening_a_run_asks_nothing_of_gerrit(tmp_path: Path) -> None:
    """A run outside any Gerrit configuration opens; only asking Gerrit would need ger.webUrl."""
    run = RunContext.open(tmp_path)

    assert run.gerrit.configured is False
    assert run.settings is run.gerrit.settings


def test_a_run_resolves_its_stack_context_once(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from gerrit_workflow_tools.core import repository as repository_module

    calls: list[object] = []
    resolve = repository_module.resolve_stack_context

    def counting(*args: object, **kwargs: object) -> object:
        calls.append(args)
        return resolve(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(repository_module, "resolve_stack_context", counting)
    run = RunContext.open(stack_repo, gerrit=ChangeStore())

    assert run.stack == run.stack == run.gerrit.stack_context()
    assert len(calls) == 1


def test_a_new_run_sees_what_an_earlier_one_changed(stack_repo: Path) -> None:
    """A run keeps its snapshot; the repository moving on is seen by the next run, not patched into this one."""
    before = RunContext.open(stack_repo, gerrit=ChangeStore())
    target = before.stack.target_branch
    branch = checked_out_branch_name(stack_repo)
    git("config", f"branch.{branch}.gerTarget", "elsewhere", cwd=stack_repo)

    after = RunContext.open(stack_repo, gerrit=ChangeStore())

    assert before.stack.target_branch == target
    assert after.stack.target_branch == "elsewhere"
