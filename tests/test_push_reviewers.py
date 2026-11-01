"""Tests for :mod:`gerrit_workflow_tools.core.push_reviewers`."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.rest import GerritApiError, change_id_for_gerrit_rest_path
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.push_reviewers import apply_reviewer_strategy_after_push_service
from gerrit_workflow_tools.core.reviewer import ReviewerStrategy, reviewer_accounts_from_change_info
from tests.change_store import ChangeStore
from tests.fixtures import _cid


_PROJECT = "testproj"
_BRANCH = "main"


def _triplet(change_id: str) -> str:
    return f"{_PROJECT}~{_BRANCH}~{change_id}"


def _reviewer_entry(username: str) -> dict[str, object]:
    return {"account": {"username": username}, "state": "REVIEWER"}


def _detail_payload(change_id: str, *, reviewers: list[dict[str, object]] | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": _triplet(change_id),
        "change_id": change_id,
        "reviewers": reviewers if reviewers is not None else [],
    }
    return payload


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
    cid = _cid("a")
    res = apply_reviewer_strategy_after_push_service(
        service,
        ReviewerStrategy.PUSH,
        ["alice"],
        [_triplet(cid)],
    )
    assert res.ok
    assert res.outcomes == []
    service.changes.get_payloads.assert_not_called()


def test_apply_reviewer_strategy_lazy_skip_and_assign() -> None:
    cid_a = _cid("a")
    cid_b = _cid("b")
    triplet_a = _triplet(cid_a)
    triplet_b = _triplet(cid_b)
    detail_empty = _detail_payload(cid_a)
    detail_has = _detail_payload(cid_b, reviewers=[_reviewer_entry("bob")])

    service = MagicMock()
    service.changes.get_payloads.return_value = {
        triplet_a: detail_empty,
        triplet_b: detail_has,
    }

    res = apply_reviewer_strategy_after_push_service(
        service, ReviewerStrategy.LAZY, ["alice", "ben"], [triplet_a, triplet_b]
    )

    assert res.ok
    assert [o.change_id for o in res.outcomes] == [cid_a, cid_b]
    assert res.outcomes[0].reviewers_assigned == ("alice", "ben")
    assert res.outcomes[1].reviewers_assigned == ()
    service.changes.set_reviewers.assert_called_once_with(triplet_a, add=["alice", "ben"], remove=[])


def test_apply_reviewer_strategy_overwrite_removes_reviewers_and_keeps_cc() -> None:
    cid = _cid("c")
    triplet = _triplet(cid)
    detail = _detail_payload(
        cid,
        reviewers=[
            {"account": {"username": "old", "_account_id": 5}, "state": "REVIEWER"},
            {"account": {"username": "gone", "_account_id": 7}, "state": "REVIEWER"},
            {"account": {"username": "watcher", "_account_id": 9}, "state": "CC"},
        ],
    )
    service = MagicMock()
    service.changes.get_payloads.return_value = {triplet: detail}

    res = apply_reviewer_strategy_after_push_service(service, ReviewerStrategy.OVERWRITE, ["alice"], [triplet])

    assert res.ok
    assert len(res.outcomes) == 1
    assert res.outcomes[0].reviewers_assigned == ("alice",)
    service.changes.set_reviewers.assert_has_calls([call(triplet, add=["alice"], remove=[5, 7])])


def test_apply_reviewer_strategy_overwrite_keeps_cc_on_change_info_map() -> None:
    """ChangeInfo stores reviewers as a ``REVIEWER`` / ``CC`` map."""

    cid = _cid("d")
    triplet = _triplet(cid)
    detail: dict[str, object] = {
        "id": triplet,
        "change_id": cid,
        "reviewers": {
            "REVIEWER": [{"_account_id": 5, "username": "old"}],
            "CC": [{"_account_id": 9, "username": "watcher"}],
        },
    }
    service = MagicMock()
    service.changes.get_payloads.return_value = {triplet: detail}

    res = apply_reviewer_strategy_after_push_service(service, ReviewerStrategy.OVERWRITE, ["alice"], [triplet])

    assert res.ok
    service.changes.set_reviewers.assert_has_calls([call(triplet, add=["alice"], remove=[5])])


def test_rejected_reviewer_is_reported_per_change_and_every_change_is_tried() -> None:
    triplet_a, triplet_b = _triplet(_cid("a")), _triplet(_cid("b"))
    service = MagicMock()
    service.changes.get_payloads.return_value = {triplet_a: {"reviewers": []}, triplet_b: {"reviewers": []}}
    service.changes.set_reviewers.side_effect = [GerritApiError("Gerrit HTTP 400"), None]

    res = apply_reviewer_strategy_after_push_service(service, ReviewerStrategy.OVERWRITE, ["x"], [triplet_a, triplet_b])

    assert res.ok is False
    assert [issue.level for issue in res.issues] == ["warning"]
    assert res.issues[0].message.startswith(f"pushed, but could not update reviewers on {triplet_a}")
    assert [outcome.change_id for outcome in res.outcomes] == [_cid("b")]


def test_set_reviewers_keeps_existing_reviewers_when_the_add_is_rejected(tmp_path: Path) -> None:
    class _RejectsAdds(ChangeStore):
        def set_reviewers_batch(self, change_id: str, **_kw: object) -> dict[str, object]:
            raise GerritApiError("Gerrit HTTP 400")

    triplet = _triplet(_cid("a"))
    store = _RejectsAdds({triplet: _detail_payload(_cid("a"), reviewers=[_reviewer_entry("admin")])})
    service = GerritService.from_cwd(tmp_path, settings=Settings.from_map({}), rest=store)

    with pytest.raises(GerritApiError):
        service.changes.set_reviewers(triplet, add=["nosuchuser"], remove=[5])

    assert store.calls_to("delete_reviewer") == []


def test_overwrite_does_not_remove_a_reviewer_it_was_asked_to_keep() -> None:
    triplet = _triplet(_cid("a"))
    service = MagicMock()
    service.changes.get_payloads.return_value = {
        triplet: {
            "reviewers": {"REVIEWER": [{"_account_id": 5, "username": "Admin"}, {"_account_id": 7, "username": "bob"}]}
        }
    }

    res = apply_reviewer_strategy_after_push_service(service, ReviewerStrategy.OVERWRITE, ["admin"], [triplet])

    assert res.ok is True
    service.changes.set_reviewers.assert_called_once_with(triplet, add=["admin"], remove=[7])


def test_lazy_says_so_when_it_skipped_every_change() -> None:
    triplet = _triplet(_cid("a"))
    service = MagicMock()
    service.changes.get_payloads.return_value = {triplet: {"reviewers": {"REVIEWER": [{"username": "admin"}]}}}

    res = apply_reviewer_strategy_after_push_service(service, ReviewerStrategy.LAZY, ["nosuchuser"], [triplet])

    assert res.ok is True
    assert [issue.level for issue in res.issues] == ["warning"]
    assert "nosuchuser not added or checked" in res.issues[0].message
