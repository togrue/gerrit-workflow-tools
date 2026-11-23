"""``ger edit``, ``ger reword`` and ``ger fix`` refuse a change Gerrit already closed (#105).

Git alone cannot tell: a change submitted since the last ``git fetch`` is still on the local
stack. All three commands agree on what they refuse and how they say it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_edit import main as gedit_main, main_reword as greword_main
from gerrit_workflow_tools.cli_fix import main as fix_main
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.git_run import git, git_out
from gerrit_workflow_tools.core.rewrite_refusal import closed_change_refusal
from gerrit_workflow_tools.core.run_context import RunContext
from gerrit_workflow_tools.core.stack import Commit
from tests.change_store import ChangeStore
from tests.cli_gerrit_mocks import build_details_by_change_id, stack_rows_mb_to_head
from tests.conftest import run_cli
from tests.test_gerrit_unavailable import _DownGerrit


MERGED = "is already merged on Gerrit"
ABANDONED = "is abandoned on Gerrit"

COMMANDS = {"edit": gedit_main, "reword": greword_main, "fix": fix_main}


@pytest.fixture
def rows(stack_repo: Path) -> list[Commit]:
    git("config", "gerrit.webUrl", "https://g.example", cwd=stack_repo)
    return stack_rows_mb_to_head(stack_repo)


@pytest.fixture
def rebases(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Every ``git rebase`` a command starts, recorded instead of run."""
    started: list[list[str]] = []
    real_run = subprocess.run

    def recording(cmd, **kwargs):  # type: ignore[no-untyped-def]
        if isinstance(cmd, list) and cmd[:2] == ["git", "rebase"]:
            started.append(cmd)
            return subprocess.CompletedProcess(cmd, 0)
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", recording)
    return started


def _store(rows: list[Commit], status: str) -> ChangeStore:
    """Gerrit with the oldest stack commit's change at *status*, the others open."""
    return ChangeStore(build_details_by_change_id(rows, per_index_overrides=[{"status": status}]))


def _run(
    command: str, repo: Path, rows: list[Commit], monkeypatch: pytest.MonkeyPatch, gerrit: object, *extra: str
) -> tuple[int, str, str]:
    if command == "fix":
        (repo / "a.txt").write_text("a fix\n", encoding="utf-8")
        git("add", "a.txt", cwd=repo)
    return run_cli(repo, COMMANDS[command], [*extra, rows[0].sha], monkeypatch, gerrit=gerrit)


def _rewrote(command: str, repo: Path, rebases: list[list[str]]) -> bool:
    if command == "fix":
        return git_out("log", "-1", "--format=%s", cwd=repo).startswith("fixup! ")
    return bool(rebases)


@pytest.mark.parametrize("command", COMMANDS)
@pytest.mark.parametrize(
    ("status", "said", "advice"),
    [("MERGED", MERGED, "`git fetch`, then `ger rebase`"), ("ABANDONED", ABANDONED, "`ger edit --drop ")],
)
def test_a_closed_change_is_refused_before_anything_is_rewritten(
    command: str,
    status: str,
    said: str,
    advice: str,
    stack_repo: Path,
    rows: list[Commit],
    rebases: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    code, _out, err = _run(command, stack_repo, rows, monkeypatch, _store(rows, status))

    assert code == ExitCode.USAGE, err
    assert f"error: {rows[0].short_sha} {said}" in err
    assert advice in err
    assert not _rewrote(command, stack_repo, rebases)


@pytest.mark.parametrize("command", COMMANDS)
def test_an_open_change_is_rewritten(
    command: str, stack_repo: Path, rows: list[Commit], rebases: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    code, _out, err = _run(command, stack_repo, rows, monkeypatch, _store(rows, "NEW"))

    assert code == 0, err
    assert _rewrote(command, stack_repo, rebases)


@pytest.mark.parametrize("command", COMMANDS)
def test_an_unreachable_gerrit_refuses_nothing_more_than_before(
    command: str, stack_repo: Path, rows: list[Commit], rebases: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The git-only check stands. ``ger fix`` never needed Gerrit for a stack commit (ADR-0003);
    ``ger edit`` and ``ger reword`` overlay the stack for the attention cursor and say Gerrit is down, as before."""
    code, _out, err = _run(command, stack_repo, rows, monkeypatch, _DownGerrit())

    assert MERGED not in err and ABANDONED not in err
    if command == "fix":
        assert code == 0, err
        assert _rewrote(command, stack_repo, rebases)
    else:
        assert code == ExitCode.GERRIT, err


@pytest.mark.parametrize("status", ["MERGED", "ABANDONED"])
def test_dropping_a_closed_change_is_what_to_do_with_it(
    status: str, stack_repo: Path, rows: list[Commit], rebases: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    _code, _out, err = _run("edit", stack_repo, rows, monkeypatch, _store(rows, status), "--drop")

    assert MERGED not in err and ABANDONED not in err
    assert rebases, err


@pytest.mark.parametrize(
    ("gerrit", "web_url"),
    [(_DownGerrit(), "https://g.example"), (None, None)],
    ids=["unreachable", "not-configured"],
)
def test_the_check_says_nothing_when_gerrit_cannot_be_asked(
    gerrit: object, web_url: str | None, stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if web_url is None:
        git("config", "--unset", "gerrit.webUrl", cwd=stack_repo, check=False)
    else:
        git("config", "gerrit.webUrl", web_url, cwd=stack_repo)
    row = stack_rows_mb_to_head(stack_repo)[0]
    monkeypatch.chdir(stack_repo)
    run = RunContext.open(stack_repo, settings=Settings.from_cwd(stack_repo), gerrit=gerrit)  # type: ignore[arg-type]

    assert closed_change_refusal(run.gerrit, row.change_id, commit=row.short_sha) is None


def test_fix_asks_once_and_then_answers_from_the_cache(
    stack_repo: Path, rows: list[Commit], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A change merged on Gerrit stays refused inside the trust window without asking again."""
    store = _store(rows, "MERGED")
    for _ in range(2):
        code, _out, err = _run("fix", stack_repo, rows, monkeypatch, store)
        assert code == ExitCode.USAGE, err
        assert MERGED in err
    assert [call.method for call in store.calls] == ["query_changes"]


def test_fix_of_a_fixup_asks_about_the_original_change(
    stack_repo: Path, rows: list[Commit], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fixup has no Change-Id of its own; the new fixup names the original, so its change decides."""
    (stack_repo / "a.txt").write_text("first fix\n", encoding="utf-8")
    git("add", "a.txt", cwd=stack_repo)
    git("commit", "-q", "--fixup", rows[0].sha, cwd=stack_repo)
    fixup = git_out("rev-parse", "HEAD", cwd=stack_repo)

    (stack_repo / "a.txt").write_text("second fix\n", encoding="utf-8")
    git("add", "a.txt", cwd=stack_repo)
    code, _out, err = run_cli(stack_repo, fix_main, [fixup], monkeypatch, gerrit=_store(rows, "MERGED"))

    assert code == ExitCode.USAGE, err
    assert f"error: {rows[0].short_sha} {MERGED}" in err
    assert git_out("rev-parse", "HEAD", cwd=stack_repo) == fixup
