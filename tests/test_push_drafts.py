# Spec: docu/spec/commands/reply.md#publishing-with-ger-push

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_push import main as push_main
from gerrit_workflow_tools.core.git_run import git
from gerrit_workflow_tools.core.stack import Commit, commits_in_range
from gerrit_workflow_tools.push_input_line import parse as parse_push_options_line
from tests.change_store import ChangeStore
from tests.cli_gerrit_mocks import at_older_patchsets, build_details_by_change_id, web_edit
from tests.conftest import run_cli


class _Tty:
    def isatty(self) -> bool:
        return True


def _pushed(stack_repo: Path, *, updated: bool = True) -> tuple[ChangeStore, list[Commit], list[str]]:
    """The ready prefix (two commits) on Gerrit, with an older patchset unless *updated* is False."""
    rows = commits_in_range(stack_repo, "main..feature")[:2]
    details = build_details_by_change_id(rows)
    if updated:
        at_older_patchsets(details, stack_repo)
    return ChangeStore(details, accounts={7: {"_account_id": 7, "username": "alice"}}), rows, list(details)


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


def _options(refspec: str | None) -> list[str]:
    assert refspec is not None
    return refspec.partition("%")[2].split(",") if "%" in refspec else []


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


@pytest.mark.parametrize(("answer", "option"), [("", "publish-comments"), ("y", "publish-comments")])
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

    def _input(prompt: str = "") -> str:
        prompts.append(prompt)
        return ""

    monkeypatch.setattr("builtins.input", _input)
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


def test_drafts_on_an_outdated_change_are_named(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An outdated change gets no patchset either, with --force pushing the commit above it: its drafts stay."""
    rows = commits_in_range(stack_repo, "main..feature")[:2]
    details = at_older_patchsets(build_details_by_change_id(rows), stack_repo)
    keys = list(details)
    web_edit(details[keys[0]], rows[0].sha)
    store = ChangeStore(details)
    _with_draft(store, keys[0])
    prompts: list[str] = []

    def _input(prompt: str = "") -> str:
        prompts.append(prompt)
        return ""

    monkeypatch.setattr(sys, "stdin", _Tty())
    monkeypatch.setattr("builtins.input", _input)
    code, out, _err, refspec = _run_push(stack_repo, monkeypatch, ["--force"], store)
    assert code == ExitCode.OK
    assert f"drafts on {rows[0].short_sha} stay drafts" in out
    assert "Drafts" not in out.replace(f"drafts on {rows[0].short_sha}", "")
    assert not any("publish" in p for p in prompts)
    assert "publish-comments" not in _options(refspec)


def _line_answers(monkeypatch: pytest.MonkeyPatch, answer: str | None) -> list[dict[str, object]]:
    """Make the confirmation the push options line; *answer* is what is typed, ``None`` takes the starting line."""
    monkeypatch.setattr(sys, "stdin", _Tty())
    monkeypatch.setattr("gerrit_workflow_tools.push_confirm.options_line_available", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *_a: pytest.fail("drafts use the push options line too"))
    calls: list[dict[str, object]] = []

    def _prompt(*_a: object, **kw: object) -> object:
        calls.append(kw)
        return parse_push_options_line(str(kw["suffix"]) if answer is None else answer)

    monkeypatch.setattr("gerrit_workflow_tools.push_confirm.prompt_reviewers_line", _prompt)
    return calls


def test_options_line_starts_with_publish_drafts(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Drafts get the usual push options line, with publish-drafts at its end: Enter publishes."""
    store, _rows, keys = _pushed(stack_repo)
    _with_draft(store, keys[0])
    calls = _line_answers(monkeypatch, None)
    code, out, _err, refspec = _run_push(stack_repo, monkeypatch, [], store)
    assert code == ExitCode.OK
    assert calls[0]["suffix"] == "publish-drafts"
    assert calls[0]["message"] == "Push options (Enter pushes, Up: history, Ctrl-C cancels): "
    assert "Drafts publish 1" in out
    assert "publish-comments" in _options(refspec)
    assert "no-publish-comments" not in _options(refspec)


@pytest.mark.parametrize("answer", ["", "alice", "publish-drafts -publish-drafts"])
def test_options_line_without_publish_drafts_keeps_them(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch, answer: str
) -> None:
    store, _rows, keys = _pushed(stack_repo)
    _with_draft(store, keys[0])
    _line_answers(monkeypatch, answer)
    code, _out, _err, refspec = _run_push(stack_repo, monkeypatch, [], store)
    assert code == ExitCode.OK
    assert "no-publish-comments" in _options(refspec)
    assert ("r=alice" in _options(refspec)) is (answer == "alice")


def test_options_line_with_other_options_and_publish_drafts(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, _rows, keys = _pushed(stack_repo)
    _with_draft(store, keys[0])
    _line_answers(monkeypatch, "alice wip publish-drafts")
    code, _out, _err, refspec = _run_push(stack_repo, monkeypatch, [], store)
    assert code == ExitCode.OK
    assert {"r=alice", "wip", "publish-comments"} <= set(_options(refspec))


def test_config_off_starts_the_line_without_publish_drafts(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, _rows, keys = _pushed(stack_repo)
    _with_draft(store, keys[0])
    git("config", "ger.pushPublishDrafts", "false", cwd=stack_repo)
    calls = _line_answers(monkeypatch, None)
    code, out, _err, refspec = _run_push(stack_repo, monkeypatch, [], store)
    assert code == ExitCode.OK
    assert calls[0]["suffix"] == ""
    assert "Drafts keep 1" in out
    assert "no-publish-comments" in _options(refspec)


def test_config_off_still_publishes_when_typed(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, _rows, keys = _pushed(stack_repo)
    _with_draft(store, keys[0])
    git("config", "ger.pushPublishDrafts", "false", cwd=stack_repo)
    _line_answers(monkeypatch, "publish-drafts")
    code, _out, _err, refspec = _run_push(stack_repo, monkeypatch, [], store)
    assert code == ExitCode.OK
    assert "publish-comments" in _options(refspec)
    assert "no-publish-comments" not in _options(refspec)


def test_publish_comments_flag_starts_the_line_with_publish_drafts(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _rows, keys = _pushed(stack_repo)
    _with_draft(store, keys[0])
    git("config", "ger.pushPublishDrafts", "false", cwd=stack_repo)
    calls = _line_answers(monkeypatch, None)
    code, _out, _err, refspec = _run_push(stack_repo, monkeypatch, ["--publish-comments"], store)
    assert code == ExitCode.OK
    assert calls[0]["suffix"] == "publish-drafts"
    assert "publish-comments" in _options(refspec)


def test_no_drafts_no_publish_drafts_on_the_line(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, _rows, _keys = _pushed(stack_repo)
    calls = _line_answers(monkeypatch, None)
    code, _out, _err, refspec = _run_push(stack_repo, monkeypatch, [], store)
    assert code == ExitCode.OK
    assert calls[0]["suffix"] == ""
    assert not any("publish-comments" in option for option in _options(refspec))


def test_guard_starts_the_line_without_publish_drafts(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, _rows, keys = _pushed(stack_repo)
    _with_draft(store, keys[0])
    monkeypatch.setenv("GER_NO_PUBLISH", "1")
    calls = _line_answers(monkeypatch, "publish-drafts")
    code, _out, _err, refspec = _run_push(stack_repo, monkeypatch, [], store)
    assert code == ExitCode.OK
    assert calls[0]["suffix"] == ""
    assert "no-publish-comments" in _options(refspec)
