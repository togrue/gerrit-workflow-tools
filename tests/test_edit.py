"""Tests for ``ger edit``."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_edit import main as gedit_main
from gerrit_workflow_tools.cli_edit import main_reword as greword_main
from gerrit_workflow_tools.cli_edit import resolve_first_edit_attention_sha
from gerrit_workflow_tools.core.config import clear_gerrit_git_config_cache
from gerrit_workflow_tools.core.git_run import git
from tests.cli_gerrit_mocks import build_details_by_change_id, patch_gerrit_client_for_queries, stack_rows_mb_to_head
from tests.conftest import run_cli


def _configure_repo(repo: Path) -> None:
    git("config", "gerrit.webUrl", "https://g.example", cwd=repo)
    clear_gerrit_git_config_cache()


def test_gedit_help(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, _err = run_cli(stack_repo, gedit_main, ["--help"], monkeypatch, catch_sys_exit=True)
    assert code == 0
    assert "[REV]" in out
    assert "reword" in out.lower()
    assert "--first-attention-commit" in out


def test_greword_help(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, _err = run_cli(stack_repo, greword_main, ["--help"], monkeypatch, catch_sys_exit=True)
    assert code == 0
    assert "ger reword" in out
    assert "--edit" in out
    assert "--drop" in out


def test_resolve_first_edit_attention_oldest_ci_failed(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    overrides: list[dict] = [{}] * len(rows)
    overrides[0] = {"verified": -1}
    if len(overrides) > 1:
        overrides[-1] = {"unresolved_comment_count": 3}
    details = build_details_by_change_id(rows, per_index_overrides=overrides)
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_log", details_by_change_id=details):
        sha = resolve_first_edit_attention_sha(stack_repo)
    assert sha == rows[0].sha


def test_resolve_first_edit_attention_none_when_green(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from gerrit_workflow_tools.core.git_run import GitError

    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    with (
        patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_log", details_by_change_id=details),
        pytest.raises(GitError, match="no commit needs edit attention"),
    ):
        resolve_first_edit_attention_sha(stack_repo)


def test_gedit_first_attention_commit_starts_rebase(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    overrides: list[dict] = [{}] * len(rows)
    overrides[0] = {"unresolved_comment_count": 2}
    details = build_details_by_change_id(rows, per_index_overrides=overrides)
    captured: dict[str, str] = {}

    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if isinstance(cmd, list) and len(cmd) >= 2 and cmd[0] == "git" and cmd[1] == "rebase":
            captured["full_sha"] = kwargs["env"]["GEDIT_FULL_SHA"]
            return subprocess.CompletedProcess(cmd, 0)
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_log", details_by_change_id=details):
        code, _out, err = run_cli(stack_repo, gedit_main, ["--first-attention-commit"], monkeypatch)
    assert code == 0, err
    assert captured["full_sha"] == rows[0].sha
