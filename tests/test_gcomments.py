from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gerrit_workflow_tools.cli_gcomments import main as gcomments_main
from gerrit_workflow_tools.git_run import git, git_out
from gerrit_workflow_tools.config import clear_gerrit_git_config_cache
from gerrit_workflow_tools.gerrit_url import resolve_gerrit_web_base
from gerrit_workflow_tools.gerrit_comments import format_human, select_commit_for_comments
from tests.conftest import json_stdout, run_cli


def test_format_human_full_no_duplicate_subject_line() -> None:
    """%B-style body includes subject as first line; --full must not print it twice."""
    out = format_human(
        [
            {
                "commit": {
                    "sha": "9663dae33ea6ba94ddcc0b8fe25b74f4d8fcc27f",
                    "subject": "docs: note change 9998",
                    "body": "docs: note change 9998\n\nChange-Id: Ief8fa7cdbbc0dbb47127eb3e2f3c8cb82a9fa97b\n",
                },
                "comments": [],
            }
        ],
        full=True,
        oneline=False,
    )
    assert out.count("docs: note change 9998") == 1
    assert "  No comments" in out


def test_format_human_prints_comments_when_present() -> None:
    out = format_human(
        [
            {
                "commit": {"sha": "abc", "subject": "sub", "body": None},
                "comments": [
                    {
                        "path": "a.py",
                        "line": 1,
                        "unresolved": True,
                        "message": "fix me",
                        "url": "",
                        "author": "A",
                        "patchSet": 1,
                        "updated": "t",
                    }
                ],
            }
        ],
        full=False,
        oneline=False,
    )
    assert "No comments" not in out
    assert "a.py:1" in out
    assert "fix me" in out


def test_resolve_gerrit_web_base_uses_web_url(stack_repo: Path) -> None:
    """gerrit.webUrl supplies the Gerrit base."""
    git("config", "gerrit.webUrl", "https://reviews.example", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    assert resolve_gerrit_web_base(stack_repo) == "https://reviews.example"


def test_resolve_gerrit_web_base_missing_raises(stack_repo: Path) -> None:
    clear_gerrit_git_config_cache()
    with pytest.raises(ValueError, match="gerrit.webUrl"):
        resolve_gerrit_web_base(stack_repo)


def test_gcomments_exits_when_web_url_missing(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clear_gerrit_git_config_cache()
    code, _out, err = run_cli(stack_repo, gcomments_main, ["--json"], monkeypatch)
    assert code != 0
    assert "gerrit.webUrl" in err


def test_select_commit_skips_fixup(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    git(
        "commit",
        "--allow-empty",
        "-m",
        "fixup! should skip\n\nChange-Id: Iaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        cwd=stack_repo,
        env=env,
    )
    sha = select_commit_for_comments(stack_repo, explicit_rev=None, skip_fixups=True)
    sub = git_out("log", "-1", "--format=%s", sha, cwd=stack_repo)
    assert not sub.startswith("fixup!")


def test_gcomments_json_mocked(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ch = {
        "id": "myproject~master~Ibbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "change_id": "Ibbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "project": "myproject",
        "_number": 42,
        "subject": "hello",
        "current_revision": "abc12345678901234567890123456789012345678",
    }
    comments = {
        "src/a.py": [
            {
                "id": "c1",
                "author": {"name": "A"},
                "updated": "2020-01-01 12:00:00.000000000",
                "line": 10,
                "message": "hi",
                "patch_set": 1,
                "unresolved": True,
            }
        ]
    }

    with (
        patch(
            "gerrit_workflow_tools.cli_gcomments.resolve_gerrit_web_base",
            return_value="https://g.example",
        ),
        patch("gerrit_workflow_tools.cli_gcomments.GerritClient") as client_cls,
    ):
        inst = MagicMock()
        client_cls.return_value = inst
        inst.query_changes.return_value = [ch]
        inst.get_related.return_value = []
        inst.get_comments.return_value = comments

        code, out, err = run_cli(
            stack_repo,
            gcomments_main,
            ["--json"],
            monkeypatch,
        )
    assert code == 0
    data = json_stdout(out)
    assert "changes" in data
    assert len(data["changes"]) == 1
    assert data["changes"][0]["changeNumber"] == 42
    assert len(data["changes"][0]["comments"]) == 1
    assert data["changes"][0]["comments"][0]["line"] == 10
