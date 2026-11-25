# Spec: docu/spec/commands/inbox.md#your-turn — whose turn a change you review is
"""Whose turn a change you review is comes from Gerrit's attention set, in one place
(``your_turn_since``), and every view says the same: the inbox's sections, its summary and
``next:`` line, the headline, and ``ger show``'s attention line.

The walk that asked for it: after the reviewer voted, the views still said "waiting for your
review", the chain stayed in *to review* with its clock restarted by the reviewer's own vote,
while Gerrit had taken the reviewer out of the attention set.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_inbox import main as inbox_main
from gerrit_workflow_tools.core.attention import Attention
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.change_view import ChangeView
from gerrit_workflow_tools.core.inbox import (
    OUTGOING,
    TO_REVIEW,
    Headline,
    InboxSection,
    build_to_review_query,
    pick_headline,
)
from gerrit_workflow_tools.core.review_chain import assemble_review_chains, your_turn_since
from tests.conftest import json_stdout, run_cli
from tests.test_inbox import SELF, _change, _store


NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
_ENTRY = {
    "account": {"_account_id": SELF},
    "last_update": "2026-08-18 09:00:00.000000000",
    "reason": "removed on reply",
}


def _reviewed(payload: dict[str, Any]) -> dict[str, Any]:
    """*payload* after your reply: Gerrit took you out of its attention set."""
    payload.pop("attention_set", None)
    return {**payload, "removed_from_attention_set": {str(SELF): _ENTRY}}


def _alice(number: int, sha: str, parent: str | None = "origin", **extra: Any) -> dict[str, Any]:
    return _change(
        number=number,
        sha=sha,
        parent=parent,
        subject=f"change {number}",
        updated="2026-08-18 10:00:00.000000000",
        **extra,
    )


def test_the_attention_set_decides_whose_turn_it_is() -> None:
    held = ChangeView.from_payload(_alice(1, "a1", attention_since="2026-08-17 12:00:00.000000000"))
    assert your_turn_since(held, SELF) == datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    # Taken out on your reply: not your turn, though you never voted and the patch set is new.
    assert your_turn_since(ChangeView.from_payload(_reviewed(_alice(1, "a1"))), SELF) is None
    # A server without an attention set: never voted, so your turn since the change was created.
    plain = ChangeView.from_payload(_alice(1, "a1"))
    assert your_turn_since(plain, SELF) == datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc)
    # Never on a change of yours, whatever the attention set says.
    mine = _alice(1, "a1", attention_since="2026-08-17 12:00:00.000000000")
    mine["owner"] = {"name": "me", "_account_id": SELF}
    assert your_turn_since(ChangeView.from_payload(mine), SELF) is None


def test_a_reviewed_chain_waits_on_its_author_and_the_queue_speaks_of_your_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    waiting = _alice(10, "w1", attention_since="2026-08-15 12:00:00.000000000")
    reviewed = _reviewed(_alice(20, "r1", comments=1))
    store = _store(waiting, reviewed)
    code, out, err = run_cli(tmp_path, inbox_main, ["--color=never", "--no-url"], monkeypatch, gerrit=store)
    assert code == int(ExitCode.OK), err
    assert "to review (2; 1 your turn)" in out
    assert [line[:3] for line in out.splitlines() if line.startswith("c")] == ["c10"]
    assert "+ 1 chain waits on the author (-v lists them)" in out
    assert re.search(r"summary: 1 chain · 1 change · longest wait \d+d\n", out), out
    assert "next: ger review c10" in out
    _code, verbose, _err = run_cli(tmp_path, inbox_main, ["--color=never", "--no-url", "-v"], monkeypatch, gerrit=store)
    [row] = [line for line in verbose.splitlines() if line.startswith("c20")]
    assert "waiting" not in row and "active" in row, "your own reply restarts no clock"

    _code, out, _err = run_cli(tmp_path, inbox_main, ["--json"], monkeypatch, gerrit=store)
    [section, _outgoing] = json_stdout(out)["sections"]
    assert [(c["top"]["number"], c["your_turn"]) for c in section["chains"]] == [(10, True), (20, False)]
    assert section["summary"]["chains"] == 1 and section["summary"]["waiting_on_author"] == 1


def test_with_nothing_your_turn_there_is_no_summary_and_no_next_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(_reviewed(_alice(20, "r1")))
    code, out, err = run_cli(tmp_path, inbox_main, ["--color=never", "--no-url"], monkeypatch, gerrit=store)
    assert code == int(ExitCode.OK), err
    assert "to review (1; none your turn)" in out
    assert "1 chain waits on the author" in out
    assert "summary:" not in out and "next:" not in out


def _sections(to_review: list[dict[str, Any]], outgoing: list[dict[str, Any]]) -> list[InboxSection]:
    def chains(rows: list[dict[str, Any]]) -> Any:
        views = [ChangeView.from_payload(row) for row in rows]
        return assemble_review_chains(views, [], web_base="https://g", now=NOW, self_account_id=SELF)

    return [InboxSection(TO_REVIEW, "", chains(to_review)), InboxSection(OUTGOING, "", chains(outgoing))]


def _mine_with_comments(updated: str) -> dict[str, Any]:
    payload = _change(number=30, sha="m1", parent="origin", subject="mine", updated=updated, comments=1)
    payload["owner"] = {"name": "me", "_account_id": SELF}
    return payload


def test_a_review_whose_turn_is_yours_ranks_beside_comments_on_your_chain_the_older_first() -> None:
    review = _alice(10, "w1", attention_since="2026-08-17 12:00:00.000000000")  # waited 1d
    older = _mine_with_comments("2026-08-16 12:00:00.000000000")  # 2d
    newer = _mine_with_comments("2026-08-18 11:00:00.000000000")  # 1h
    headline = pick_headline(_sections([review], [older]))
    assert headline is not None and headline.reason == Attention.UNRESOLVED_COMMENTS
    headline = pick_headline(_sections([review], [newer]))
    assert isinstance(headline, Headline) and (headline.reason, headline.member.number) == (
        Attention.WAITING_ON_YOU,
        10,
    )


def test_a_chain_waiting_on_its_author_is_never_the_headline() -> None:
    assert pick_headline(_sections([_reviewed(_alice(10, "w1"))], [])) is None


def test_the_headline_opens_the_base_most_change_whose_turn_is_yours() -> None:
    base = _reviewed(_alice(10, "b1"))
    top = _alice(11, "b2", parent="b1", attention_since="2026-08-17 12:00:00.000000000")
    headline = pick_headline(_sections([base, top], []))
    assert isinstance(headline, Headline) and headline.member.number == 11


def test_a_chain_in_the_queue_is_shown_whole_its_unready_changes_said_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new patchset at the bottom lost the Verified vote above it: the chain stays whole
    (finding 3 of the 2026-11-25 review walk), its changes that are not ready marked."""
    base = _alice(40, "c1", attention_since="2026-08-17 12:00:00.000000000")
    middle = _alice(41, "c2", parent="c1", verified=0, attention_since="2026-08-17 12:00:00.000000000")
    top = _alice(42, "c3", parent="c2", verified=0)
    alone = _alice(50, "x1", verified=0)  # a chain with nothing ready: counted, not listed
    store = _store(base, middle, top, alone, stub=[base])
    unready = build_to_review_query(Settings.from_cwd(tmp_path), projects=[], include_unready=True)
    store.stub_query(unready, [base, middle, top, alone])
    code, out, err = run_cli(tmp_path, inbox_main, ["--color=never", "--no-url"], monkeypatch, gerrit=store)
    assert code == int(ExitCode.OK), err
    assert "to review (1; 1 not ready, --unready shows it)" in out
    assert "c42  3 changes" in out
    assert "  └ c41  v?  cr?       # not ready: not verified yet" in out
    assert "  └ c42  v?  cr?       # not ready: not verified yet" in out
    assert "c50" not in out

    _code, out, _err = run_cli(tmp_path, inbox_main, ["--json"], monkeypatch, gerrit=store)
    [chain] = json_stdout(out)["sections"][0]["chains"]
    assert [(m["number"], m["not_ready"], m["your_turn"]) for m in chain["members"]] == [
        (40, None, True),
        (41, "not verified yet", False),  # Gerrit's attention set holds you, but it is not in your queue
        (42, "not verified yet", False),
    ]
