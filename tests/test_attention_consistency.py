"""Cross-command attention agreement: ``ger log``, ``ger show`` and ``ger ci`` say the same of a commit.

Each judges a commit of the local stack within the whole stack, so a change blocked by one
below it, or one that blocks those above it, reads the same in all three. Same pattern as
``tests/test_change_resolution_consistency.py``: agreement between commands, not per command.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_ci import main as ci_main
from gerrit_workflow_tools.cli_log import main as log_main
from gerrit_workflow_tools.cli_show import main as show_main
from gerrit_workflow_tools.core.git_run import git
from tests.change_store import ChangeStore
from tests.cli_gerrit_mocks import build_details_by_change_id, stack_rows_mb_to_head
from tests.conftest import json_stdout, run_cli


def _hint(line: str) -> str:
    """The trailing ``# …`` hints of a row or header; empty without."""
    return line.split("  # ", 1)[1].strip() if "  # " in line else ""


@pytest.fixture
def blocked_stack(stack_repo: Path) -> tuple[Path, ChangeStore]:
    """Four changes on Gerrit: the bottom one waits for review, the second fails CI, the top two are submittable."""
    git("config", "ger.stopPattern", "^nothing-matches$", cwd=stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    overrides = [{"cr": 1, "submittable": False}, {"verified": -1, "submittable": False}, {}, {}]
    return stack_repo, ChangeStore(build_details_by_change_id(rows, per_index_overrides=overrides))


def test_log_show_and_ci_agree_on_every_commit_of_a_blocked_chain(
    blocked_stack: tuple[Path, ChangeStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, store = blocked_stack
    rows = stack_rows_mb_to_head(repo)
    _code, log_out, err = run_cli(repo, log_main, ["--color", "never"], monkeypatch, gerrit=store)
    assert _code == 0, err
    # A log row is ``sha tokens # subject  # hints``: the hints follow the subject.
    in_log = {line.split()[0]: _hint(line.split(" # ", 1)[1]) for line in log_out.splitlines()[: len(rows)]}
    _code, ci_out, err = run_cli(repo, ci_main, ["--stack", "--color", "never"], monkeypatch, gerrit=store)
    assert _code == 0, err
    in_ci = {line.split()[1]: _hint(line) for line in ci_out.splitlines() if line.startswith("commit ")}
    for back, row in enumerate(reversed(rows)):
        _code, show_out, err = run_cli(repo, show_main, [f"HEAD~{back}", "--color", "never"], monkeypatch, gerrit=store)
        assert _code == 0, err
        in_show = _hint(show_out.splitlines()[0])
        assert in_log[row.short_sha] == in_show == in_ci[row.short_sha], row.short_sha
    assert in_log[rows[0].short_sha] == "awaiting review"  # the blocker says why
    assert in_log[rows[-1].short_sha] == "submittable"


def test_show_names_the_blocker_and_its_json_agrees_with_log(
    blocked_stack: tuple[Path, ChangeStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, store = blocked_stack
    rows = stack_rows_mb_to_head(repo)
    _code, out, _err = run_cli(repo, show_main, ["HEAD", "--color", "never"], monkeypatch, gerrit=store)
    assert f"chain blocked by {rows[0].short_sha} (awaiting review)" in out.splitlines()
    _code, show_json, _err = run_cli(repo, show_main, ["HEAD", "--json"], monkeypatch, gerrit=store)
    _code, log_json, _err = run_cli(repo, log_main, ["--json"], monkeypatch, gerrit=store)
    head = json_stdout(log_json)["commits"][-1]
    shown = json_stdout(show_json)
    assert shown["attention_reasons"] == head["attention_reasons"]
    assert "chain-blocked" in shown["attention_reasons"]


def test_show_of_a_commit_the_upstream_contains_says_merged(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """HEAD is origin/main: no change on Gerrit, and not "not pushed" either: the upstream has it."""
    git("update-ref", "refs/remotes/origin/main", "HEAD", cwd=stack_repo)
    code, out, err = run_cli(stack_repo, show_main, ["HEAD", "--color", "never"], monkeypatch, gerrit=ChangeStore({}))
    assert code == 0, err
    head = out.splitlines()[0]
    assert head.split()[2] == "m" and head.endswith("# merged"), head
    assert "not pushed" not in out


def test_a_detached_head_no_branch_points_at_is_one_message_and_one_exit_code(
    blocked_stack: tuple[Path, ChangeStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    """ger log failed in git (exit 7) and ger show / ger ci as an unresolved changeish (3): now one usage error."""
    from gerrit_workflow_tools.cli_common import ExitCode

    repo, store = blocked_stack
    git("checkout", "-q", "--detach", "HEAD~1", cwd=repo)
    answers = {
        (main.__module__, tuple(argv)): run_cli(repo, main, argv, monkeypatch, gerrit=store)
        for main, argv in ((log_main, []), (log_main, ["origin/main..HEAD"]), (show_main, []), (ci_main, []))
    }
    assert {code for code, _out, _err in answers.values()} == {ExitCode.USAGE}
    messages = {err.strip() for _code, _out, err in answers.values()}
    assert len(messages) == 1, messages
    (message,) = messages
    assert message.startswith("error: HEAD is detached and no local branch points at it")
    assert message.endswith("check out the branch you work on: git switch <branch>")
