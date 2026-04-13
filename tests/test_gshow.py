from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gerrit_workflow_tools.cli_gshow import main as gshow_main
from gerrit_workflow_tools.config import clear_gerrit_git_config_cache
from gerrit_workflow_tools.git_run import git
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


def test_gshow_json_numeric_change_mocked(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git("config", "gerrit.webUrl", "https://g.example", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    cid = "Ibbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    sha = "abc12345678901234567890123456789012345678"
    ch = _detail_ok(change_id=cid, sha=sha, cr_value=2)
    with (
        patch(
            "gerrit_workflow_tools.cli_gshow.resolve_gerrit_web_base",
            return_value="https://g.example",
        ),
        patch("gerrit_workflow_tools.cli_gshow.GerritClient") as client_cls,
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


def test_gshow_json_attention_mocked(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git("config", "gerrit.webUrl", "https://g.example", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    cid = "Ibbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    sha = "abc12345678901234567890123456789012345678"
    ch = _detail_ok(change_id=cid, sha=sha, cr_value=1)
    with (
        patch(
            "gerrit_workflow_tools.cli_gshow.resolve_gerrit_web_base",
            return_value="https://g.example",
        ),
        patch("gerrit_workflow_tools.cli_gshow.GerritClient") as client_cls,
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


def test_gshow_comment_tail_in_json(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
            "gerrit_workflow_tools.cli_gshow.resolve_gerrit_web_base",
            return_value="https://g.example",
        ),
        patch("gerrit_workflow_tools.cli_gshow.GerritClient") as client_cls,
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


def test_gshow_full_comment_json(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
            "gerrit_workflow_tools.cli_gshow.resolve_gerrit_web_base",
            return_value="https://g.example",
        ),
        patch("gerrit_workflow_tools.cli_gshow.GerritClient") as client_cls,
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
