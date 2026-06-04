"""Tests for :mod:`gerrit_workflow_tools.core.push_reviewers`."""

from __future__ import annotations

from unittest.mock import MagicMock, call

from gerrit_workflow_tools.core.gerrit.rest import change_id_for_gerrit_rest_path
from gerrit_workflow_tools.core.push_reviewers import apply_reviewer_strategy_after_push_service
from gerrit_workflow_tools.core.reviewer import ReviewerStrategy, reviewer_accounts_from_change_info


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


def test_reviewer_accounts_from_change_info_dict_shaped_reviewers() -> None:
    """Gerrit change detail often uses a ``reviewers.REVIEWER`` / ``reviewers.CC`` map."""

    detail: dict[str, object] = {
        "reviewers": {
            "REVIEWER": [{"_account_id": 1, "username": "alice"}],
            "CC": [{"_account_id": 2, "email": "bob@example.com"}],
        }
    }
    accs = reviewer_accounts_from_change_info(detail)
    assert [a.slug for a in accs] == ["alice", "bob"]
    assert [a.account_id for a in accs] == [1, 2]


def test_apply_reviewer_strategy_push_has_no_outcomes() -> None:
    service = MagicMock()
    res = apply_reviewer_strategy_after_push_service(
        service,
        ReviewerStrategy.PUSH,
        ["alice"],
        ["Ia" * 20 + "a"],
    )
    assert res.ok
    assert res.outcomes == []
    service.changes.get_payloads.assert_not_called()


def test_apply_reviewer_strategy_lazy_skip_and_assign() -> None:
    cid_a = "Ia" * 20 + "a"
    cid_b = "Ib" * 20 + "b"
    detail_empty: dict[str, object] = {"reviewers": []}
    detail_has: dict[str, object] = {"reviewers": [_reviewer_entry("bob")]}

    service = MagicMock()
    service.changes.get_payloads.return_value = {
        change_id_for_gerrit_rest_path(cid_a): detail_empty,
        change_id_for_gerrit_rest_path(cid_b): detail_has,
    }

    res = apply_reviewer_strategy_after_push_service(service, ReviewerStrategy.LAZY, ["alice", "ben"], [cid_a, cid_b])

    assert res.ok
    assert [o.change_id for o in res.outcomes] == [cid_a, cid_b]
    assert res.outcomes[0].reviewers_assigned == ("alice", "ben")
    assert res.outcomes[1].reviewers_assigned == ()
    service.changes.set_reviewers.assert_called_once_with(cid_a, add=["alice", "ben"], remove=[])


def test_apply_reviewer_strategy_overwrite_removes_and_adds() -> None:
    cid = "Ic" * 20 + "c"
    detail: dict[str, object] = {
        "reviewers": [
            {"account": {"username": "old", "_account_id": 5}, "state": "REVIEWER"},
            {"account": {"username": "gone", "_account_id": 7}, "state": "REVIEWER"},
        ]
    }
    service = MagicMock()
    service.changes.get_payloads.return_value = {change_id_for_gerrit_rest_path(cid): detail}

    res = apply_reviewer_strategy_after_push_service(service, ReviewerStrategy.OVERWRITE, ["alice"], [cid])

    assert res.ok
    assert len(res.outcomes) == 1
    assert res.outcomes[0].reviewers_assigned == ("alice",)
    service.changes.set_reviewers.assert_has_calls([call(cid, add=["alice"], remove=[5, 7])])
