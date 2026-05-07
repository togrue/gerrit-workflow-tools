"""Tests for :mod:`gerrit_workflow_tools.core.push_reviewers`."""

from __future__ import annotations

from unittest.mock import MagicMock

from gerrit_workflow_tools.core.gerrit_client import GerritClient, change_id_for_gerrit_rest_path
from gerrit_workflow_tools.core.push_reviewers import apply_reviewer_strategy_after_push
from gerrit_workflow_tools.core.reviewer import ReviewerStrategy


def _reviewer_entry(username: str) -> dict[str, object]:
    return {"account": {"username": username}, "state": "REVIEWER"}


def test_change_id_for_gerrit_rest_path_uppercases_leading_i() -> None:
    low = "i" + "a" * 40
    assert change_id_for_gerrit_rest_path(low) == "I" + "a" * 40
    assert change_id_for_gerrit_rest_path("I" + "b" * 40) == "I" + "b" * 40


def test_change_id_for_gerrit_rest_path_leaves_numeric_and_triplet_unchanged() -> None:
    assert change_id_for_gerrit_rest_path("12345") == "12345"
    assert change_id_for_gerrit_rest_path("proj~main~Iaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa") == (
        "proj~main~Iaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    )


def test_apply_reviewer_strategy_push_has_no_outcomes() -> None:
    client = MagicMock(spec=GerritClient)
    res = apply_reviewer_strategy_after_push(
        client,
        ReviewerStrategy.PUSH,
        ["alice"],
        ["Ia" * 20 + "a"],
    )
    assert res.ok
    assert res.outcomes == []
    client.get_change.assert_not_called()


def test_apply_reviewer_strategy_lazy_skip_and_assign() -> None:
    cid_a = "Ia" * 20 + "a"
    cid_b = "Ib" * 20 + "b"
    detail_empty: dict[str, object] = {"reviewers": []}
    detail_has: dict[str, object] = {"reviewers": [_reviewer_entry("bob")]}

    client = MagicMock(spec=GerritClient)
    client.get_change.side_effect = [detail_empty, detail_has]

    res = apply_reviewer_strategy_after_push(client, ReviewerStrategy.LAZY, ["alice", "ben"], [cid_a, cid_b])

    assert res.ok
    assert [o.change_id for o in res.outcomes] == [cid_a, cid_b]
    assert res.outcomes[0].reviewers_assigned == ("alice", "ben")
    assert res.outcomes[1].reviewers_assigned == ()
    assert client.add_reviewer.call_count == 2
    client.add_reviewer.assert_any_call(cid_a, "alice")
    client.add_reviewer.assert_any_call(cid_a, "ben")


def test_apply_reviewer_strategy_overwrite_removes_and_adds() -> None:
    cid = "Ic" * 20 + "c"
    detail: dict[str, object] = {
        "reviewers": [
            {"account": {"username": "old", "_account_id": 5}, "state": "REVIEWER"},
            {"account": {"username": "gone", "_account_id": 7}, "state": "REVIEWER"},
        ]
    }
    client = MagicMock(spec=GerritClient)
    client.get_change.return_value = detail

    res = apply_reviewer_strategy_after_push(client, ReviewerStrategy.OVERWRITE, ["alice"], [cid])

    assert res.ok
    assert len(res.outcomes) == 1
    assert res.outcomes[0].reviewers_assigned == ("alice",)
    assert {c.args for c in client.delete_reviewer.call_args_list} == {(cid, 5), (cid, 7)}
    client.add_reviewer.assert_called_once_with(cid, "alice")
