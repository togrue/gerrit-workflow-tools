"""``ger push`` at its prompt: answers, help, and reviewers Gerrit has no account for.

Spec: docu/spec/commands/push.md § Confirmation output. Each test runs the command's ``main`` with
``ChangeStore`` as Gerrit and the push options line answered in turn.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_push import main as push_main
from gerrit_workflow_tools.core.stack import commits_in_range
from gerrit_workflow_tools.push_input_line import parse
from gerrit_workflow_tools.push_input_prompt import LINE_HELP
from tests.change_store import ChangeStore
from tests.cli_gerrit_mocks import at_older_patchsets, build_details_by_change_id
from tests.conftest import run_cli


ALICE = {7: {"_account_id": 7, "username": "alice"}}


class _Tty:
    def isatty(self) -> bool:
        return True


def _store(stack_repo: Path, *, updated: bool = True, wip: bool = False) -> tuple[ChangeStore, list[str]]:
    """The ready prefix (two commits) on Gerrit: at an older patchset (*updated*), else as it is here."""
    rows = commits_in_range(stack_repo, "main..feature")[:2]
    details = build_details_by_change_id(rows, per_index_overrides=[{"work_in_progress": wip}] * 2)
    if updated:
        at_older_patchsets(details, stack_repo)
    return ChangeStore(details, accounts=ALICE), list(details)


def _answering(monkeypatch: pytest.MonkeyPatch, *answers: str) -> tuple[MagicMock, list[dict[str, object]]]:
    """The confirmation is the push options line, typed *answers* in turn; ``git push`` is mocked."""
    monkeypatch.setattr(sys, "stdin", _Tty())
    monkeypatch.setattr("gerrit_workflow_tools.push_confirm.options_line_available", lambda: True)
    pending, calls = list(answers), []

    def _prompt(*_a: object, **kw: object) -> object:
        calls.append(kw)
        assert pending, "the prompt opened again with no answer left"
        return parse(pending.pop(0))

    monkeypatch.setattr("gerrit_workflow_tools.push_confirm.prompt_reviewers_line", _prompt)
    mock_run = MagicMock(return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""))
    monkeypatch.setattr("gerrit_workflow_tools.core.push.run_git_push", mock_run)
    return mock_run, calls


def _refspec(mock_run: MagicMock) -> str:
    return str(mock_run.call_args[0][0][-1])


def _rest_writes(store: ChangeStore) -> list[str]:
    writes = ("set_wip", "set_private", "set_topic", "set_reviewers_batch", "delete_reviewer")
    return [call.method for call in store.calls if call.method in writes]


@pytest.mark.parametrize("answer", ["n", "no", "q", "quit"])
def test_no_cancels_and_sets_nothing(stack_repo: Path, monkeypatch: pytest.MonkeyPatch, answer: str) -> None:
    """Before, `n` was the reviewer `n`: `--ready` was set over REST, then `r=n` failed (exit 5)."""
    store, _keys = _store(stack_repo, updated=False, wip=True)
    mock_run, _calls = _answering(monkeypatch, answer)
    code, _out, err = run_cli(stack_repo, push_main, ["--ready"], monkeypatch, gerrit=store)
    assert code == ExitCode.OK
    assert "Push cancelled." in err
    mock_run.assert_not_called()
    assert _rest_writes(store) == []


@pytest.mark.parametrize("answer", ["y", "yes", ""])
def test_yes_pushes_with_no_reviewer(stack_repo: Path, monkeypatch: pytest.MonkeyPatch, answer: str) -> None:
    store, _keys = _store(stack_repo)
    mock_run, _calls = _answering(monkeypatch, answer)
    code, _out, _err = run_cli(stack_repo, push_main, [], monkeypatch, gerrit=store)
    assert code == ExitCode.OK
    assert "r=" not in _refspec(mock_run)


def test_help_prints_the_grammar_and_asks_again(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Before, `?` became `%r=?` and git refused the refspec (exit 128)."""
    store, _keys = _store(stack_repo)
    mock_run, calls = _answering(monkeypatch, "r=alice ?", "")
    code, out, _err = run_cli(stack_repo, push_main, [], monkeypatch, gerrit=store)
    assert code == ExitCode.OK
    assert LINE_HELP in out
    assert calls[1]["text"] == "r=alice"  # the line as typed, without the question
    assert mock_run.call_count == 1


def test_an_unknown_reviewer_keeps_the_prompt_and_applies_nothing(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _keys = _store(stack_repo, updated=False, wip=True)
    mock_run, calls = _answering(monkeypatch, "nosuch", "q")
    code, _out, err = run_cli(stack_repo, push_main, ["--ready"], monkeypatch, gerrit=store)
    assert code == ExitCode.OK
    assert (
        "error: Gerrit has no account `nosuch`; nothing was pushed or set. Fix the name, or drop it with -nosuch" in err
    )
    assert calls[1]["text"] == "r=nosuch"
    mock_run.assert_not_called()
    assert _rest_writes(store) == []


def test_a_known_reviewer_is_not_asked_about(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, _keys = _store(stack_repo)
    mock_run, _calls = _answering(monkeypatch, "alice")
    code, _out, _err = run_cli(stack_repo, push_main, [], monkeypatch, gerrit=store)
    assert code == ExitCode.OK
    assert _refspec(mock_run).endswith("%r=alice")


@pytest.mark.parametrize("argv", [["-y"], ["--dry-run"]])
def test_off_a_terminal_an_unknown_reviewer_is_a_usage_error_before_any_push(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    store, _keys = _store(stack_repo, updated=False, wip=True)
    mock_run = MagicMock()
    monkeypatch.setattr("gerrit_workflow_tools.core.push.run_git_push", mock_run)
    code, _out, err = run_cli(
        stack_repo, push_main, [*argv, "--ready", "--reviewers", "alice,nosuch"], monkeypatch, gerrit=store
    )
    assert code == ExitCode.USAGE
    assert "error: Gerrit has no account `nosuch`; nothing was pushed or set" in err
    mock_run.assert_not_called()
    assert _rest_writes(store) == []


def test_lazy_reviewers_are_not_checked_first(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """lazy and overwrite add over REST, where a group is a reviewer too: Gerrit judges those."""
    store, _keys = _store(stack_repo)
    monkeypatch.setattr(
        "gerrit_workflow_tools.core.push.run_git_push",
        MagicMock(return_value=subprocess.CompletedProcess([], 0, "", "")),
    )
    run_cli(
        stack_repo, push_main, ["-y", "--reviewers", "devs", "--reviewer-strategy", "lazy"], monkeypatch, gerrit=store
    )
    assert store.calls_to("get_account") == []


def test_yes_after_the_prefilled_publish_drafts_publishes(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Before, `y` typed after the starting `publish-drafts` became the reviewer `publish-draftsy`."""
    store, keys = _store(stack_repo)
    store.set_drafts(keys[0], {"f.py": [{"id": "d-1", "line": 1, "message": "Done", "patch_set": 1}]})
    mock_run, calls = _answering(monkeypatch, "publish-drafts y")
    code, _out, _err = run_cli(stack_repo, push_main, [], monkeypatch, gerrit=store)
    assert code == ExitCode.OK
    assert calls[0]["suffix"] == "publish-drafts"
    assert _refspec(mock_run).split("%")[1] == "publish-comments"
