# Spec: docu/spec/commands/log.md, show.md, ci.md
# Covers: rows fitted to the terminal, the chain blocker, next steps, wording of ger log / show / ci

"""What ``ger log``, ``ger show`` and ``ger ci`` tell the author at each step (mocked Gerrit)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gerrit_workflow_tools.cli_log import main as log_main
from gerrit_workflow_tools.core.git_run import git
from gerrit_workflow_tools.render import style
from tests.change_store import ChangeStore
from tests.cli_gerrit_mocks import build_details_by_change_id, stack_rows_mb_to_head
from tests.conftest import run_cli


LONG = "Teach the parser to recover from a missing closing bracket in nested argument lists"


def _on_terminal(monkeypatch: pytest.MonkeyPatch, columns: int) -> None:
    """Output goes to a terminal *columns* wide (colour and links off, so the text can be compared)."""
    monkeypatch.setattr(style, "_stdout_is_tty", lambda: True)
    monkeypatch.setenv("COLUMNS", str(columns))
    monkeypatch.setenv("LINES", "50")


def _store(repo: Path, overrides: list[dict[str, Any]]) -> ChangeStore:
    rows = stack_rows_mb_to_head(repo)
    return ChangeStore(build_details_by_change_id(rows, per_index_overrides=overrides))


def _long_subject_stack(repo: Path) -> None:
    git("commit", "--allow-empty", "-m", f"{LONG}\n\nChange-Id: I{'7' * 40}", cwd=repo)


def test_log_on_a_terminal_cuts_subjects_so_rows_and_hints_fit(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _long_subject_stack(stack_repo)
    store = _store(stack_repo, [{}, {"verified": -1, "submittable": False}, {}, {}, {}])
    _on_terminal(monkeypatch, 80)
    code, out, err = run_cli(
        stack_repo, log_main, ["--color", "never", "--hyperlinks", "never"], monkeypatch, gerrit=store
    )
    assert code == 0, err
    lines = out.splitlines()
    assert all(len(line) <= 80 for line in lines), "\n".join(line for line in lines if len(line) > 80)
    long_row = next(line for line in lines if line.startswith(stack_rows_mb_to_head(stack_repo)[-1].short_sha))
    assert "Teach the parser" in long_row
    assert "…" in long_row
    assert "# build failed" in out  # the hint is not pushed off the screen


def test_log_piped_is_never_cut(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _long_subject_stack(stack_repo)
    store = _store(stack_repo, [{}, {"verified": -1, "submittable": False}, {}, {}, {}])
    monkeypatch.setenv("COLUMNS", "80")
    code, out, err = run_cli(stack_repo, log_main, [], monkeypatch, gerrit=store)
    assert code == 0, err
    assert LONG in out
    assert "…" not in out
