# Spec: docu/spec/commands/reply.md#publishing-with-ger-push

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_push import main as push_main
from gerrit_workflow_tools.core.stack import Commit, commits_in_range
from tests.change_store import ChangeStore
from tests.cli_gerrit_mocks import build_details_by_change_id
from tests.conftest import run_cli


class _Tty:
    def isatty(self) -> bool:
        return True


def _pushed(stack_repo: Path, *, updated: bool = True) -> tuple[ChangeStore, list[Commit], list[str]]:
    """The ready prefix (two commits) on Gerrit, with an older patchset unless *updated* is False."""
    rows = commits_in_range(stack_repo, "main..feature")[:2]
    details = build_details_by_change_id(rows)
    if updated:
        for payload in details.values():
            payload["current_revision"] = "0" * 40
            payload["revisions"] = {"0" * 40: {"_number": 1}}
    return ChangeStore(details), rows, list(details)


def _with_draft(store: ChangeStore, key: str) -> None:
    store.set_drafts(key, {"f.py": [{"id": "d-1", "line": 1, "message": "Done", "unresolved": False, "patch_set": 1}]})


def _run_push(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str], store: ChangeStore
) -> tuple[int, str, str, str | None]:
    mock_run = MagicMock(return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""))
    monkeypatch.setattr("gerrit_workflow_tools.core.push.run_git_push", mock_run)
    code, out, err = run_cli(stack_repo, push_main, argv, monkeypatch, gerrit=store, catch_sys_exit=True)
    refspec = mock_run.call_args[0][0][-1] if mock_run.called else None
    return code, out, err, refspec


def _options(refspec: str) -> list[str]:
    return refspec.partition("%")[2].split(",") if "%" in refspec else []


def test_yes_keeps_drafts_unless_asked(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Scripts and agents never publish by accident, even with an account preference that would."""
    store, _rows, keys = _pushed(stack_repo)
    _with_draft(store, keys[0])
    code, _out, _err, refspec = _run_push(stack_repo, monkeypatch, ["-y"], store)
    assert code == ExitCode.OK
    assert "no-publish-comments" in _options(refspec)


def test_yes_with_publish_comments_publishes(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, _rows, keys = _pushed(stack_repo)
    _with_draft(store, keys[0])
    code, _out, _err, refspec = _run_push(stack_repo, monkeypatch, ["-y", "--publish-comments"], store)
    assert code == ExitCode.OK
    assert "publish-comments" in _options(refspec)
    assert "no-publish-comments" not in _options(refspec)


def test_no_drafts_sends_no_comment_option(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, _rows, _keys = _pushed(stack_repo)
    _code, _out, _err, refspec = _run_push(stack_repo, monkeypatch, ["-y"], store)
    assert not any("publish-comments" in option for option in _options(refspec))


@pytest.mark.parametrize(
    ("answer", "option"), [("", "publish-comments"), ("y", "publish-comments"), ("p", "no-publish-comments")]
)
def test_prompt_offers_to_publish_the_drafts(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch, answer: str, option: str
) -> None:
    store, _rows, keys = _pushed(stack_repo)
    _with_draft(store, keys[0])
    prompts: list[str] = []

    def _input(prompt: str = "") -> str:
        prompts.append(prompt)
        return answer

    monkeypatch.setattr(sys, "stdin", _Tty())
    monkeypatch.setattr("builtins.input", _input)
    code, out, _err, refspec = _run_push(stack_repo, monkeypatch, [], store)
    assert code == ExitCode.OK
    assert any("Push and publish 1 draft(s)? [Y/n/p/r]" in p for p in prompts)
    assert "Drafts keep 1" in out
    assert option in _options(refspec)
    assert ("no-publish-comments" in _options(refspec)) is (option == "no-publish-comments")


def test_guard_blocks_publish_comments(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, _rows, keys = _pushed(stack_repo)
    _with_draft(store, keys[0])
    monkeypatch.setenv("GER_NO_PUBLISH", "1")
    code, _out, err, refspec = _run_push(stack_repo, monkeypatch, ["-y", "--publish-comments"], store)
    assert code == ExitCode.USAGE
    assert "GER_NO_PUBLISH" in err
    assert refspec is None


def test_guard_never_offers_to_publish(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, _rows, keys = _pushed(stack_repo)
    _with_draft(store, keys[0])
    monkeypatch.setenv("GER_NO_PUBLISH", "1")
    prompts: list[str] = []
    monkeypatch.setattr(sys, "stdin", _Tty())
    monkeypatch.setattr("builtins.input", lambda prompt="": prompts.append(prompt) or "")
    code, _out, _err, refspec = _run_push(stack_repo, monkeypatch, [], store)
    assert code == ExitCode.OK
    assert not any("publish" in p for p in prompts)
    assert "no-publish-comments" in _options(refspec)


def test_drafts_on_unchanged_commits_are_named(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Gerrit publishes only on changes the push updates; the preview says which drafts stay."""
    store, rows, keys = _pushed(stack_repo, updated=False)
    _with_draft(store, keys[0])
    code, out, _err, _refspec = _run_push(stack_repo, monkeypatch, ["--dry-run"], store)
    assert code == ExitCode.OK
    assert f"drafts on {rows[0].short_sha} stay drafts" in out
    assert "Drafts" not in out.replace(f"drafts on {rows[0].short_sha}", "")
