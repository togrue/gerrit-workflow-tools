"""One reason, one message, one code — whichever command meets it.

Pins agreement *between* commands for the refusals the shared runtime owns: outside a git
repository, a branch without an upstream, a work tree a history rewrite would trample, and a
prompt with no terminal to answer it.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_edit import main as edit_main, main_reword
from gerrit_workflow_tools.cli_fix import main as fix_main
from gerrit_workflow_tools.cli_ger import main as ger_main
from gerrit_workflow_tools.cli_log import main as log_main
from gerrit_workflow_tools.cli_push import main as push_main
from gerrit_workflow_tools.cli_rebase import main as rebase_main
from gerrit_workflow_tools.cli_show import main as show_main
from gerrit_workflow_tools.core.git_run import git
from tests.change_store import ChangeStore
from tests.conftest import run_cli


def _configure(repo: Path) -> None:
    git("config", "gerrit.webUrl", "https://g.example", cwd=repo)


@pytest.mark.parametrize(
    ("main_fn", "argv"),
    [
        (log_main, []),
        (show_main, []),
        (edit_main, []),
        (main_reword, ["HEAD"]),
        (fix_main, ["HEAD"]),
        (push_main, ["--dry-run"]),
        (rebase_main, []),
    ],
)
def test_every_command_says_not_a_git_repository_the_same_way(
    main_fn: Callable[..., int], argv: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "plain"
    outside.mkdir()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    code, out, err = run_cli(outside, main_fn, argv, monkeypatch)
    assert (code, out, err.strip()) == (ExitCode.GIT, "", "error: not a git repository; run ger inside a clone")


@pytest.mark.parametrize(("main_fn", "argv"), [(log_main, []), (push_main, ["--dry-run"]), (push_main, ["-y"])])
def test_a_missing_upstream_reads_the_same_in_log_and_push(
    main_fn: Callable[..., int], argv: list[str], stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(stack_repo)
    git("branch", "--unset-upstream", cwd=stack_repo)
    code, _out, err = run_cli(stack_repo, main_fn, argv, monkeypatch)
    assert code == ExitCode.CONFIG
    assert err.startswith("error: No upstream configured for branch 'feature'.")
    assert "git branch --set-upstream-to=<remote>/<branch>" in err


@pytest.mark.parametrize(("main_fn", "argv"), [(edit_main, ["HEAD~1"]), (main_reword, ["HEAD~1"]), (rebase_main, [])])
def test_every_history_rewrite_refuses_a_dirty_tree_the_same_way(
    main_fn: Callable[..., int], argv: list[str], stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(stack_repo)
    tracked = git("ls-files", cwd=stack_repo).stdout.split()[0]
    (stack_repo / tracked).write_text("uncommitted\n", encoding="utf-8")
    code, _out, err = run_cli(stack_repo, main_fn, argv, monkeypatch, gerrit=ChangeStore({}))
    assert code == ExitCode.USAGE
    assert err.strip() == (
        "error: you have uncommitted changes; commit them (`ger fix REV` folds them into a stack commit) "
        "or `git stash` them first"
    )


def test_unknown_command_suggests_the_close_one(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, _out, err = run_cli(stack_repo, ger_main, ["lgo"], monkeypatch)
    assert code == ExitCode.USAGE
    assert "error: unknown command 'lgo'; did you mean 'log'?" in err


def test_ready_boundary_rev_is_a_usage_error_that_names_the_way_out(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``stack_repo``'s third commit matches the stop pattern, so ``--all`` would push past it."""
    _configure(stack_repo)
    code, _out, err = run_cli(stack_repo, push_main, ["--dry-run", "HEAD"], monkeypatch)
    assert code == ExitCode.USAGE
    assert "is at or after the ready boundary" in err
    assert "or pass --all to push past it" in err
    assert "\\\\" not in err, "the stop pattern is quoted as written, not repr()-escaped"


def test_errors_come_after_what_the_command_printed(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-TTY push without -y: the listing, then the reason it stopped."""
    import io
    import sys

    _configure(stack_repo)
    shared = io.StringIO()

    class _Tee(io.StringIO):
        def write(self, s: str) -> int:
            return shared.write(s)

    monkeypatch.chdir(stack_repo)
    monkeypatch.setattr(sys, "stdout", _Tee())
    monkeypatch.setattr(sys, "stderr", _Tee())
    code = push_main([], gerrit=ChangeStore({}))
    text = shared.getvalue()
    assert code == ExitCode.USAGE
    assert text.rstrip().endswith("error: non-interactive stdin: use --yes (-y) to push without a confirmation prompt")
    assert "About to push" in text
