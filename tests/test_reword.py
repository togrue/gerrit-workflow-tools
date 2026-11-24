"""``ger reword``: only messages change, no rebase; the message from -m / -F, else an editor, never nothing.

Before, ``ger reword REV </dev/null`` exited 0 having rewritten the stack for nothing (new ids for
the commit and every one above it, all ahead of Gerrit), or stopped mid-rebase on "Terminal is dumb".
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_edit import main as edit_main, main_reword
from gerrit_workflow_tools.core.git_run import git, git_out
from gerrit_workflow_tools.core.rebase_state import rebase_in_progress
from tests.change_store import ChangeStore
from tests.conftest import run_cli
from tests.test_rebasing_stack import _load, _store


def _shas(repo: Path) -> list[str]:
    return git_out("rev-list", "@{u}..HEAD", cwd=repo).split()


def _message(repo: Path, rev: str) -> str:
    return git_out("log", "-1", "--format=%B", rev, cwd=repo)


def _reword(repo: Path, argv: list[str], monkeypatch: pytest.MonkeyPatch, store: ChangeStore | None = None):
    return run_cli(repo, main_reword, argv, monkeypatch, catch_sys_exit=True, gerrit=store or ChangeStore({}))


@pytest.fixture(autouse=True)
def _no_editor(monkeypatch: pytest.MonkeyPatch) -> None:
    """No terminal (tests never have one) and no editor configured, as in CI and an agent's shell."""
    for name in ("GIT_EDITOR", "GIT_SEQUENCE_EDITOR", "VISUAL", "EDITOR"):
        monkeypatch.delenv(name, raising=False)


def test_without_a_terminal_or_a_message_it_refuses_and_rewrites_nothing(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _shas(stack_repo)

    code, _out, err = _reword(stack_repo, ["HEAD~1"], monkeypatch)

    assert code == ExitCode.USAGE
    assert err.strip() == (
        "error: no terminal to edit the message in: give it with -m MSG or -F FILE ('-': stdin), "
        "or set GIT_EDITOR to an editor that runs without one"
    )
    assert _shas(stack_repo) == before and not rebase_in_progress(stack_repo)


def test_m_rewords_keeping_the_change_id_and_says_how_to_take_it_back(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(stack_repo)
    _load(stack_repo, store)  # the cache a `ger log` leaves: which changes are on Gerrit
    change_id = _message(stack_repo, "HEAD~1").splitlines()[-1]
    old, tip = git_out("rev-parse", "--short", "HEAD~1", cwd=stack_repo), _shas(stack_repo)[0]

    code, _out, err = _reword(stack_repo, ["-m", "Better subject", "-m", "A body.", "HEAD~1"], monkeypatch, store)

    assert code == 0, err
    assert _message(stack_repo, "HEAD~1") == f"Better subject\n\nA body.\n\n{change_id}"
    new = git_out("rev-parse", "--short", "HEAD~1", cwd=stack_repo)
    assert err.splitlines() == [
        f"reworded {old} → {new} Better subject",
        f"to take it back: git reset --keep {tip[:7]}",
        "reword finished: rewrote 2 of 4 commits, all now ahead of Gerrit",
        "next: `ger push` uploads the stack as it is now",
    ]


def test_the_same_message_leaves_every_commit_as_it_was(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    before = _shas(stack_repo)
    message = _message(stack_repo, "HEAD~1")

    code, _out, err = _reword(stack_repo, ["-m", message, "HEAD~1"], monkeypatch)

    assert code == 0, err
    assert _shas(stack_repo) == before
    subject = message.splitlines()[0]
    assert err.strip().endswith(f"{subject} is unchanged: the stack is as it was")


def test_file_dash_reads_the_message_from_stdin(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("From stdin\n"))

    code, _out, err = _reword(stack_repo, ["-F", "-", "HEAD"], monkeypatch)

    assert code == 0, err
    assert _message(stack_repo, "HEAD").splitlines()[0] == "From stdin"


def test_an_editor_set_for_scripts_writes_the_message(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``GIT_EDITOR`` runs without a terminal: it gets the message, the comment lines are left out."""
    monkeypatch.setenv("GIT_EDITOR", "sed -i -e '1s/^/edited: /'")

    code, _out, err = _reword(stack_repo, ["HEAD"], monkeypatch)

    assert code == 0, err
    message = _message(stack_repo, "HEAD")
    assert message.startswith("edited: Cleanup after experiment\n") and "#" not in message


def test_uncommitted_changes_are_no_obstacle_and_stay(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tracked = git("ls-files", cwd=stack_repo).stdout.split()[0]
    (stack_repo / tracked).write_text("uncommitted\n", encoding="utf-8")

    code, _out, err = _reword(stack_repo, ["-m", "Reworded", "HEAD~2"], monkeypatch)

    assert code == 0, err
    assert (stack_repo / tracked).read_text(encoding="utf-8") == "uncommitted\n"
    assert git_out("log", "-1", "--format=%s", "HEAD~2", cwd=stack_repo) == "Reworded"


def test_edit_takes_a_message_only_for_a_reword(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, _out, err = run_cli(stack_repo, edit_main, ["-m", "x", "HEAD"], monkeypatch, catch_sys_exit=True)
    assert code == ExitCode.USAGE
    assert "-m and -F give a reword its message" in err

    code, _out, err = run_cli(
        stack_repo, edit_main, ["--reword", "-m", "Via edit", "HEAD"], monkeypatch, gerrit=ChangeStore({})
    )
    assert code == 0, err
    assert git_out("log", "-1", "--format=%s", cwd=stack_repo) == "Via edit"
