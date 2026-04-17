"""Tests for ``git glog`` (mocked Gerrit)."""

from __future__ import annotations

from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_glog import main as glog_main
from gerrit_workflow_tools.config import clear_gerrit_git_config_cache
from gerrit_workflow_tools.git_run import git, git_out
from gerrit_workflow_tools.stack import parse_change_id
from tests.cli_gerrit_mocks import (
    build_details_by_change_id,
    patch_gerrit_client_for_queries,
    stack_rows_mb_to_head,
)
from tests.conftest import json_stdout, run_cli


def _configure_repo(repo: Path) -> None:
    git("config", "gerrit.webUrl", "https://g.example", cwd=repo)
    clear_gerrit_git_config_cache()


def test_glog_help(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, _err = run_cli(stack_repo, glog_main, ["--help"], monkeypatch, catch_sys_exit=True)
    assert code == 0
    assert "git glog" in out or "glog" in out
    assert "REV_RANGE" in out
    assert "--full" in out
    assert "--json" in out
    assert "--show-change-id" in out
    assert "--show-url" in out


@pytest.mark.parametrize(
    "argv_extra",
    [
        [],
        ["--full"],
        ["--full", "--oneline"],
        ["--full", "--compact"],
        ["--full", "--url"],
        ["--full", "--no-color"],
    ],
)
def test_glog_smoke_argv_exits_zero(
    stack_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    argv_extra: list[str],
) -> None:
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_glog", details_by_change_id=details):
        code, _out, err = run_cli(stack_repo, glog_main, argv_extra, monkeypatch)
    assert code in (0, 1), (code, err)


def test_glog_full_text_contains_commit_lines_and_summary(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_glog", details_by_change_id=details):
        code, out, err = run_cli(stack_repo, glog_main, ["--full"], monkeypatch)
    assert code == 0, err
    assert "summary:" in out
    assert "ready-to-push:" in out and " / " in out
    for _sha, short, subj, _raw in rows:
        assert short in out
        assert subj in out


def test_glog_json_full_lists_all_commits(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_glog", details_by_change_id=details):
        code, out, err = run_cli(stack_repo, glog_main, ["--json", "--full"], monkeypatch)
    assert code == 0, err
    data = json_stdout(out)
    assert isinstance(data, list)
    assert len(data) == len(rows)
    for item in data:
        assert "sha" in item
        assert "patchset_status" in item
        assert "attention_reasons" in item
        assert "change_id" in item


def test_glog_default_hides_when_all_green(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With no attention, non-``--full`` mode prints no per-commit lines."""
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_glog", details_by_change_id=details):
        code, out, err = run_cli(stack_repo, glog_main, [], monkeypatch)
    assert code == 0, err
    assert "summary:" in out
    _, first_short, first_subj, _ = rows[0]
    assert first_short not in out
    assert first_subj not in out


def test_glog_shows_attention_without_full(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One commit with CR+1 should appear without ``--full``."""
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    overrides: list[dict] = [{}] * len(rows)
    if overrides:
        overrides[-1] = {"cr": 1}
    details = build_details_by_change_id(rows, per_index_overrides=overrides)
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_glog", details_by_change_id=details):
        code, out, err = run_cli(stack_repo, glog_main, [], monkeypatch)
    assert code == 1, err
    last = rows[-1]
    assert last[1] in out
    assert "cr+1" in out


def test_glog_explicit_revset(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    mb = git_out("merge-base", "main", "HEAD", cwd=stack_repo)
    revset = f"{mb}..HEAD"
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_glog", details_by_change_id=details):
        code, out, err = run_cli(stack_repo, glog_main, ["--full", revset], monkeypatch)
    assert code == 0, err
    assert "summary:" in out


def test_glog_missing_gerrit_url(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clear_gerrit_git_config_cache()
    code, _out, err = run_cli(stack_repo, glog_main, ["--full"], monkeypatch)
    assert code == 3
    assert "error" in err.lower()


def test_glog_show_change_id_appends_token(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_glog", details_by_change_id=details):
        code, out, err = run_cli(stack_repo, glog_main, ["--full", "--show-change-id", "--no-color"], monkeypatch)
    assert code == 0, err
    _, _short, _subj, raw = rows[0]
    cid = parse_change_id(raw)
    assert cid
    assert cid[:12] in out


def test_glog_config_default_show_url(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    git("config", "gerrit.glogShowUrl", "true", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_glog", details_by_change_id=details):
        code, out, err = run_cli(stack_repo, glog_main, ["--full", "--no-color"], monkeypatch)
    assert code == 0, err
    assert "g.example" in out or "/+/" in out
