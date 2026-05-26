"""Minimal smoke per major command: log, show, rebase --help (live Gerrit)."""

from __future__ import annotations

import secrets

import pytest

from gerrit_workflow_tools.cli_log import main as ger_log_main
from gerrit_workflow_tools.cli_rebase import main as ger_rebase_main
from gerrit_workflow_tools.cli_show import main as ger_show_main
from gerrit_workflow_tools.core.git_run import git_out
from tests.conftest import run_cli
from tests.integration.integration_helpers import prepare_topic_repo
from tests.integration.repo_builder import build_linear_chain


def test_ger_log_smoke_on_topic_repo(
    tmp_path,
    gerrit_integration_context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    topic = f"log_smoke_{secrets.token_hex(4)}"
    repo = prepare_topic_repo(gerrit_integration_context, tmp_path, topic)
    build_linear_chain(repo, ["smoke commit one"])
    code, _out, err = run_cli(repo, ger_log_main, ["--color", "never"], monkeypatch)
    assert code in (0, 1), err


def test_ger_show_smoke_on_topic_repo(
    tmp_path,
    gerrit_integration_context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    topic = f"show_smoke_{secrets.token_hex(4)}"
    repo = prepare_topic_repo(gerrit_integration_context, tmp_path, topic)
    build_linear_chain(repo, ["smoke commit for show"])
    tip = git_out("rev-parse", "HEAD", cwd=repo).strip()
    code, out, err = run_cli(repo, ger_show_main, ["--color", "never", tip], monkeypatch)
    assert code in (0, 1), err
    assert tip[:8] in out or "commit" in out.lower()


def test_ger_rebase_help_exits_zero(
    tmp_path,
    gerrit_integration_context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    topic = f"rebase_help_{secrets.token_hex(4)}"
    repo = prepare_topic_repo(gerrit_integration_context, tmp_path, topic)
    code, out, _err = run_cli(repo, ger_rebase_main, ["--help"], monkeypatch, catch_sys_exit=True)
    assert code == 0
    assert "rebase" in out.lower()
