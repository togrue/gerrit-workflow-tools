from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gerrit_workflow_tools.cli_show import main as gshow_main
from gerrit_workflow_tools.cli_style import ANSI_YELLOW
from gerrit_workflow_tools.config import clear_gerrit_git_config_cache
from gerrit_workflow_tools.gerrit_change_status import LOG_QUERY_OPTIONS, norm_change_id
from gerrit_workflow_tools.git_run import git, git_out
from tests.cli_gerrit_mocks import (
    change_info_for_sha,
    head_change_id,
    patch_gerrit_client_for_queries,
)
from tests.conftest import json_stdout, run_cli


def _detail_ok(
    *,
    change_id: str,
    sha: str,
    cr_value: int = 2,
    v_value: int = 1,
) -> dict:
    """Minimal ChangeInfo for :func:`fetch_gerrit_data`."""
    return {
        "id": f"proj~master~{change_id}",
        "change_id": change_id,
        "project": "proj",
        "_number": 99,
        "subject": "subj",
        "current_revision": sha,
        "submittable": True,
        "unresolved_comment_count": 0,
        "revisions": {sha: {"_number": 1}},
        "labels": {
            "Verified": {"value": v_value, "all": [{"value": v_value}]},
            "Code-Review": {"value": cr_value, "all": [{"value": cr_value}]},
        },
    }


def test_gshow_rejects_range(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "gerrit.webUrl", "https://g.example", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    code, _out, err = run_cli(stack_repo, gshow_main, ["main..HEAD"], monkeypatch)
    assert code == 2
    assert "range" in err.lower()


def test_gshow_json_change_id_asks_gerrit_for_current_revision(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: bare ``changes/?q=`` omits ``current_revision`` unless ``o=CURRENT_REVISION``."""
    git("config", "gerrit.webUrl", "https://g.example", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    cid = "Ibbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    sha = "abc12345678901234567890123456789012345678"
    ch = _detail_ok(change_id=cid, sha=sha, cr_value=2)
    with (
        patch(
            "gerrit_workflow_tools.cli_show.resolve_gerrit_web_base",
            return_value="https://g.example",
        ),
        patch("gerrit_workflow_tools.cli_show.GerritClient") as client_cls,
    ):
        inst = MagicMock()
        client_cls.return_value = inst
        inst.query_changes.return_value = [ch]
        inst.get_comments.return_value = {}
        code, out, _err = run_cli(
            stack_repo,
            gshow_main,
            ["--json", cid],
            monkeypatch,
        )
    assert code == 0
    data = json_stdout(out)
    assert data["sha"] == sha
    # resolve_change_for_gcomments + fetch_gerrit_data batch_load each query the change
    first = inst.query_changes.call_args_list[0]
    assert first.kwargs.get("options") == list(LOG_QUERY_OPTIONS)
    assert all("CURRENT_REVISION" in (c.kwargs.get("options") or []) for c in inst.query_changes.call_args_list)


def test_gshow_json_numeric_change_mocked(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "gerrit.webUrl", "https://g.example", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    cid = "Ibbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    sha = "abc12345678901234567890123456789012345678"
    ch = _detail_ok(change_id=cid, sha=sha, cr_value=2)
    with (
        patch(
            "gerrit_workflow_tools.cli_show.resolve_gerrit_web_base",
            return_value="https://g.example",
        ),
        patch("gerrit_workflow_tools.cli_show.GerritClient") as client_cls,
    ):
        inst = MagicMock()
        client_cls.return_value = inst
        inst.query_changes.return_value = [ch]
        inst.get_comments.return_value = {}
        code, out, _err = run_cli(
            stack_repo,
            gshow_main,
            ["--json", "42"],
            monkeypatch,
        )
    assert code == 0
    data = json_stdout(out)
    assert data["change_id"] == cid
    assert data["patchset_status"] == "active"
    assert data["local_commit"] is False
    assert data["attention_reasons"] == []


def test_gshow_json_attention_mocked(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "gerrit.webUrl", "https://g.example", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    cid = "Ibbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    sha = "abc12345678901234567890123456789012345678"
    ch = _detail_ok(change_id=cid, sha=sha, cr_value=1)
    with (
        patch(
            "gerrit_workflow_tools.cli_show.resolve_gerrit_web_base",
            return_value="https://g.example",
        ),
        patch("gerrit_workflow_tools.cli_show.GerritClient") as client_cls,
    ):
        inst = MagicMock()
        client_cls.return_value = inst
        inst.query_changes.return_value = [ch]
        inst.get_comments.return_value = {}
        code, out, _err = run_cli(
            stack_repo,
            gshow_main,
            ["--json", "42"],
            monkeypatch,
        )
    assert code == 1
    data = json_stdout(out)
    assert "awaiting-review" in data["attention_reasons"]


def test_gshow_comment_tail_in_json(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "gerrit.webUrl", "https://g.example", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    cid = "Ibbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    sha = "abc12345678901234567890123456789012345678"
    ch = _detail_ok(change_id=cid, sha=sha, cr_value=2)
    long_msg = "\n".join(f"line{i}" for i in range(15))
    comments = {
        "f.py": [
            {
                "line": 1,
                "message": long_msg,
                "unresolved": True,
            }
        ]
    }
    with (
        patch(
            "gerrit_workflow_tools.cli_show.resolve_gerrit_web_base",
            return_value="https://g.example",
        ),
        patch("gerrit_workflow_tools.cli_show.GerritClient") as client_cls,
    ):
        inst = MagicMock()
        client_cls.return_value = inst
        inst.query_changes.return_value = [ch]
        inst.get_comments.return_value = comments
        code, out, _err = run_cli(
            stack_repo,
            gshow_main,
            ["--json", "42", "--comment-tail-lines", "3"],
            monkeypatch,
        )
    assert code == 0
    data = json_stdout(out)
    assert data["comments"][0]["truncated"] is True
    assert "lines omitted above" in data["comments"][0]["body"]
    assert "line14" in data["comments"][0]["body"]


def test_gshow_full_comment_json(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "gerrit.webUrl", "https://g.example", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    cid = "Ibbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    sha = "abc12345678901234567890123456789012345678"
    ch = _detail_ok(change_id=cid, sha=sha, cr_value=2)
    long_msg = "\n".join(f"line{i}" for i in range(15))
    comments = {
        "f.py": [
            {
                "line": 1,
                "message": long_msg,
                "unresolved": True,
            }
        ]
    }
    with (
        patch(
            "gerrit_workflow_tools.cli_show.resolve_gerrit_web_base",
            return_value="https://g.example",
        ),
        patch("gerrit_workflow_tools.cli_show.GerritClient") as client_cls,
    ):
        inst = MagicMock()
        client_cls.return_value = inst
        inst.query_changes.return_value = [ch]
        inst.get_comments.return_value = comments
        code, out, _err = run_cli(
            stack_repo,
            gshow_main,
            ["--json", "42", "--full"],
            monkeypatch,
        )
    assert code == 0
    data = json_stdout(out)
    assert data["comments"][0]["truncated"] is False
    assert "line0" in data["comments"][0]["body"]
    assert "line14" in data["comments"][0]["body"]


def _configure_gshow_repo(stack_repo: Path) -> None:
    git("config", "gerrit.webUrl", "https://g.example", cwd=stack_repo)
    clear_gerrit_git_config_cache()


def test_gshow_help(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, _err = run_cli(stack_repo, gshow_main, ["--help"], monkeypatch, catch_sys_exit=True)
    assert code == 0
    assert "gshow" in out.lower() or "ger show" in out
    assert "[REV]" in out


def test_gshow_human_head_formatting(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Text mode includes commit line, subject, and status prefix (no TTY colors)."""
    _configure_gshow_repo(stack_repo)
    sha = git_out("rev-parse", "HEAD", cwd=stack_repo)
    subj = git_out("log", "-1", "--format=%s", cwd=stack_repo)
    cid = head_change_id(stack_repo)
    detail = change_info_for_sha(sha, cid, number=77)
    details = {norm_change_id(cid): detail}
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_show", details_by_change_id=details):
        code, out, err = run_cli(stack_repo, gshow_main, [], monkeypatch)
    assert code == 0, err
    assert "commit " in out and sha in out
    assert sha[:8] in out
    assert subj in out
    assert "g.example/c/" in out or "/+/" in out


def test_gshow_highlights_warning_pattern_on_summary_line(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_gshow_repo(stack_repo)
    sha = git_out("rev-parse", "HEAD", cwd=stack_repo)
    subj = git_out("log", "-1", "--format=%s", cwd=stack_repo)
    cid = head_change_id(stack_repo)
    git("config", "--unset-all", "gerrit.stopPattern", cwd=stack_repo, check=False)
    git("config", "--add", "gerrit.stopPattern", r"^does-not-match$", cwd=stack_repo)
    git("config", "--unset-all", "gerrit.warningPattern", cwd=stack_repo, check=False)
    git("config", "--add", "gerrit.warningPattern", subj, cwd=stack_repo)
    clear_gerrit_git_config_cache()
    detail = change_info_for_sha(sha, cid, number=91)
    details = {norm_change_id(cid): detail}
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_show", details_by_change_id=details):
        code, out, err = run_cli(stack_repo, gshow_main, ["--color", "always"], monkeypatch)
    assert code == 0, err
    assert ANSI_YELLOW in out
    assert subj in out


@pytest.mark.parametrize(
    "argv",
    [
        ["--json", "HEAD"],
        ["HEAD", "--color=never"],
        ["HEAD", "--verbose"],
    ],
)
def test_gshow_smoke_argv_head_mocked(stack_repo: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
    _configure_gshow_repo(stack_repo)
    sha = git_out("rev-parse", "HEAD", cwd=stack_repo)
    cid = head_change_id(stack_repo)
    detail = change_info_for_sha(sha, cid, number=88)
    details = {norm_change_id(cid): detail}
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_show", details_by_change_id=details):
        code, _out, err = run_cli(stack_repo, gshow_main, argv, monkeypatch)
    assert code in (0, 1), err
