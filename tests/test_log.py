"""Tests for ``ger log`` (mocked Gerrit)."""

from __future__ import annotations

from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_log import main as log_main
from gerrit_workflow_tools.cli_style import ANSI_YELLOW
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


def test_log_help(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, _err = run_cli(stack_repo, log_main, ["--help"], monkeypatch, catch_sys_exit=True)
    assert code == 0
    assert "ger log" in out or "log" in out
    assert "REV_RANGE" in out
    assert "--full" in out
    assert "--json" in out
    assert "--show-change-id" in out
    assert "--show-url" in out
    assert "--verbose" in out or "-v" in out


@pytest.mark.parametrize(
    "argv_extra",
    [
        [],
        ["--full"],
        ["--full", "-v"],
        ["--full", "--url"],
        ["--full", "--color=never"],
    ],
)
def test_log_smoke_argv_exits_zero(
    stack_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    argv_extra: list[str],
) -> None:
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_log", details_by_change_id=details):
        code, _out, err = run_cli(stack_repo, log_main, argv_extra, monkeypatch)
    assert code in (0, 1), (code, err)


def test_log_full_text_contains_commit_lines_and_summary(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_log", details_by_change_id=details):
        code, out, err = run_cli(stack_repo, log_main, ["--full"], monkeypatch)
    assert code == 0, err
    assert "summary:" in out
    assert "ready" in out and "/" in out
    for _sha, short, subj, _raw in rows:
        assert short in out
        assert subj in out


def test_log_highlights_warning_pattern_in_summary(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    first_subject = rows[0][2]
    git("config", "--unset-all", "gerrit.stopPattern", cwd=stack_repo, check=False)
    git("config", "--add", "gerrit.stopPattern", r"^does-not-match$", cwd=stack_repo)
    git("config", "--unset-all", "gerrit.warningPattern", cwd=stack_repo, check=False)
    git("config", "--add", "gerrit.warningPattern", first_subject, cwd=stack_repo)
    clear_gerrit_git_config_cache()
    details = build_details_by_change_id(rows)
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_log", details_by_change_id=details):
        code, out, err = run_cli(stack_repo, log_main, ["--full", "--color", "always"], monkeypatch)
    assert code == 0, err
    assert ANSI_YELLOW in out
    assert first_subject in out


def test_log_full_text_uses_separate_detail_lines(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``--verbose``: oneline row with attention; indented URL; no duplicate comment-count detail line."""
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    overrides: list[dict] = [{} for _ in rows]
    overrides[0] = {"verified": -1, "submittable": False}
    overrides[1] = {"verified": 0, "cr": 0, "unresolved_comment_count": 2, "submittable": False}
    overrides[-1] = {"status": "ABANDONED", "submittable": False}
    details = build_details_by_change_id(rows, per_index_overrides=overrides)
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_log", details_by_change_id=details):
        code, out, err = run_cli(stack_repo, log_main, ["--full", "--verbose", "--color=never"], monkeypatch)
    assert code == 1, err
    assert "v? " in out
    assert "cr? " in out
    assert "# submittable" in out
    assert "build failed" in out
    assert "2 unresolved comments" in out
    assert "# comments:" not in out
    assert "# abandoned" in out
    assert "g.example" in out or "/+/" in out
    assert "✓" not in out


def test_log_json_full_lists_all_commits(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_log", details_by_change_id=details):
        code, out, err = run_cli(stack_repo, log_main, ["--json", "--full"], monkeypatch)
    assert code == 0, err
    data = json_stdout(out)
    assert isinstance(data, list)
    assert len(data) == len(rows)
    for item in data:
        assert "sha" in item
        assert "patchset_status" in item
        assert "attention_reasons" in item
        assert "change_id" in item


def test_log_default_hides_when_all_green(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With no attention, non-``--full`` mode prints no per-commit lines."""
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_log", details_by_change_id=details):
        code, out, err = run_cli(stack_repo, log_main, [], monkeypatch)
    assert code == 0, err
    assert "summary:" in out
    _, first_short, first_subj, _ = rows[0]
    assert first_short not in out
    assert first_subj not in out


def test_log_shows_attention_without_full(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One commit with CR+1 should appear without ``--full``."""
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    overrides: list[dict] = [{}] * len(rows)
    if overrides:
        overrides[-1] = {"cr": 1}
    details = build_details_by_change_id(rows, per_index_overrides=overrides)
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_log", details_by_change_id=details):
        code, out, err = run_cli(stack_repo, log_main, [], monkeypatch)
    assert code == 1, err
    last = rows[-1]
    assert last[1] in out
    assert "cr+1" in out


def test_log_explicit_revset(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    mb = git_out("merge-base", "main", "HEAD", cwd=stack_repo)
    revset = f"{mb}..HEAD"
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_log", details_by_change_id=details):
        code, out, err = run_cli(stack_repo, log_main, ["--full", revset], monkeypatch)
    assert code == 0, err
    assert "summary:" in out


def test_log_missing_gerrit_url(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clear_gerrit_git_config_cache()
    code, _out, err = run_cli(stack_repo, log_main, ["--full"], monkeypatch)
    assert code == 3
    assert "error" in err.lower()


def test_log_show_change_id_appends_token(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_log", details_by_change_id=details):
        code, out, err = run_cli(stack_repo, log_main, ["--full", "--show-change-id", "--color=never"], monkeypatch)
    assert code == 0, err
    _, _short, _subj, raw = rows[0]
    cid = parse_change_id(raw)
    assert cid
    assert cid[:12] in out


def _unicode_strikethrough(s: str) -> str:
    return "".join(f"{c}\u0336" for c in s)


def test_log_abandoned_strikes_summary(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Abandoned Gerrit changes render the subject with strike-through (no TTY: combining chars)."""
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    overrides: list[dict] = [{}] * len(rows)
    if overrides:
        overrides[-1] = {"status": "ABANDONED"}
    details = build_details_by_change_id(rows, per_index_overrides=overrides)
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_log", details_by_change_id=details):
        code, out, err = run_cli(stack_repo, log_main, ["--full", "--color=never"], monkeypatch)
    assert code == 1, err
    _sha, _short, subj, _raw = rows[-1]
    assert _unicode_strikethrough(subj) in out


def test_log_json_includes_abandoned(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    overrides = [{}] * len(rows)
    overrides[-1] = {"status": "ABANDONED"}
    details = build_details_by_change_id(rows, per_index_overrides=overrides)
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_log", details_by_change_id=details):
        code, out, err = run_cli(stack_repo, log_main, ["--json", "--full"], monkeypatch)
    assert code == 1, err
    data = json_stdout(out)
    assert data[-1]["abandoned"] is True
    assert data[0]["abandoned"] is False


def test_log_config_default_show_url(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    git("config", "gerrit.logShowUrl", "true", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_log", details_by_change_id=details):
        code, out, err = run_cli(stack_repo, log_main, ["--full", "--color=never"], monkeypatch)
    assert code == 0, err
    assert "g.example" in out or "/+/" in out
