"""A new change on top of a reviewed chain gets the chain's people (bug 5 of the rework walk).

Before: "Reviewers none added, on Gerrit: pg-alice, pg-bob, pg-ci" read as if all were covered,
while the change the push created got nobody.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from gerrit_workflow_tools.cli_push import main as push_main
from gerrit_workflow_tools.core.gerrit.change_view import ChangeView
from gerrit_workflow_tools.core.git_run import git
from gerrit_workflow_tools.core.push_reviewers import chain_people
from gerrit_workflow_tools.core.stack import Commit, commits_in_range
from tests.change_store import ChangeStore
from tests.cli_gerrit_mocks import build_details_by_change_id
from tests.conftest import run_cli


BOB, CI, CAROL, BOT = 8, 9, 10, 11


def _reviewed_bottom(rows: list[Commit]) -> dict[str, dict[str, Any]]:
    """Both commits as changes; the bottom one reviewed by Bob (Code-Review -1), CI (Verified only), a
    bot Gerrit tags as a service user, and Carol in CC."""
    reviewers = [
        {"account": {"_account_id": BOB, "username": "pg-bob"}, "state": "REVIEWER"},
        {"account": {"_account_id": CI, "username": "pg-ci"}, "state": "REVIEWER"},
        {"account": {"_account_id": BOT, "username": "lint-bot", "tags": ["SERVICE_USER"]}, "state": "REVIEWER"},
        {"account": {"_account_id": CAROL, "username": "carol"}, "state": "CC"},
    ]
    details = build_details_by_change_id(rows, per_index_overrides=[{"reviewers": reviewers}, {"reviewers": []}])
    bottom = next(iter(details.values()))
    bottom["labels"] = {
        "Code-Review": {"all": [{"_account_id": BOB, "value": -1}, {"_account_id": CI, "value": 0}]},
        "Verified": {"all": [{"_account_id": CI, "value": 1}]},
    }
    return details


def _creating(store: ChangeStore, details: dict[str, dict[str, Any]], number: int) -> MagicMock:
    """``git push``, mocked: Gerrit then has change *number* of *details*, as the push created it."""

    def push(*_args: object, **_kw: object) -> subprocess.CompletedProcess[str]:
        store.add_change(next(detail for detail in details.values() if detail["_number"] == number))
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    return MagicMock(side_effect=push)


@pytest.fixture
def chain(stack_repo: Path) -> tuple[Path, list[Commit], dict[str, dict[str, Any]]]:
    git("config", "ger.user", "me", cwd=stack_repo)
    git("config", "ger.password", "secret", cwd=stack_repo)
    git("config", "ger.webUrl", "https://g.example.test", cwd=stack_repo)
    rows = commits_in_range(stack_repo, "main..feature")[:2]
    return stack_repo, rows, _reviewed_bottom(rows)


def test_the_chains_people_leave_out_ccs_bots_and_a_ci_that_only_verifies(
    chain: tuple[Path, list[Commit], dict[str, dict[str, Any]]],
) -> None:
    _repo, _rows, details = chain
    views = [ChangeView.from_payload(detail) for detail in details.values()]
    assert chain_people(views) == ("pg-bob",)
    assert chain_people(views, you="PG-BOB") == ()


def test_the_preview_proposes_the_chains_people_for_the_new_change(
    chain: tuple[Path, list[Commit], dict[str, dict[str, Any]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, rows, details = chain
    bottom = {key: detail for key, detail in details.items() if detail["_number"] == 100}
    code, out, err = run_cli(repo, push_main, ["--dry-run", rows[1].sha], monkeypatch, gerrit=ChangeStore(bottom))
    assert code == 0, err
    lines = out.splitlines()
    new = next(i for i, line in enumerate(lines) if line.endswith(f"# {rows[1].subject}  new change"))
    assert lines[new + 1].strip() == "new change: reviewers pg-bob, as on the rest of the chain"
    assert "Reviewers none added, on Gerrit: carol, lint-bot, pg-bob, pg-ci; new change: +pg-bob  |" in out
    assert "[dry-run] after a successful push would add pg-bob to the new change" in err


def test_without_them_the_preview_says_plainly_the_new_change_gets_nobody(
    chain: tuple[Path, list[Commit], dict[str, dict[str, Any]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, rows, details = chain
    bottom = {key: detail for key, detail in details.items() if detail["_number"] == 100}
    args = ["--dry-run", "--no-chain-reviewers", rows[1].sha]
    code, out, err = run_cli(repo, push_main, args, monkeypatch, gerrit=ChangeStore(bottom))
    assert code == 0, err
    assert "new change: no reviewers (chain-reviewers on the options line adds pg-bob)" in out
    assert "; new change: none  |" in out
    # Reviewers the push names go to every change it sends, the new one too: nothing is proposed.
    args = ["--dry-run", "--reviewers", "dave", rows[1].sha]
    _code, out, _err = run_cli(repo, push_main, args, monkeypatch, gerrit=ChangeStore(bottom))
    assert "new change:" not in out


def test_the_push_adds_them_to_the_change_it_created_and_nowhere_else(
    chain: tuple[Path, list[Commit], dict[str, dict[str, Any]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, rows, details = chain
    bottom = {key: detail for key, detail in details.items() if detail["_number"] == 100}
    store = ChangeStore(bottom)
    monkeypatch.setattr("gerrit_workflow_tools.core.push.run_git_push", _creating(store, details, 101))
    code, out, err = run_cli(repo, push_main, ["-y", rows[1].sha], monkeypatch, gerrit=store)
    assert code == 0, err
    added = store.calls_to("set_reviewers_batch")
    assert [(call.args[0].endswith(rows[1].change_id or "?"), call.kwargs["reviewers"]) for call in added] == [
        (True, ["pg-bob"])
    ]
    assert "added pg-bob to change 101, as on the rest of the chain" in out


@pytest.mark.parametrize(("typed", "added"), [("chain-reviewers", True), ("", False)])
def test_the_options_line_starts_with_chain_reviewers_and_deleting_it_drops_them(
    typed: str,
    added: bool,
    chain: tuple[Path, list[Commit], dict[str, dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.test_push_prompt import _answering

    repo, rows, details = chain
    store = ChangeStore({key: detail for key, detail in details.items() if detail["_number"] == 100})
    _mock_run, calls = _answering(monkeypatch, typed)
    monkeypatch.setattr("gerrit_workflow_tools.core.push.run_git_push", _creating(store, details, 101))
    code, _out, err = run_cli(repo, push_main, [rows[1].sha], monkeypatch, gerrit=store)
    assert code == 0, err
    assert calls[0]["suffix"] == "chain-reviewers"
    assert bool(store.calls_to("set_reviewers_batch")) is added
