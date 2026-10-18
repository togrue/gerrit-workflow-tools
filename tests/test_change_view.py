"""ChangeView: one ChangeInfo payload, parsed once, for the overlay, review chains and ``ger show``."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from gerrit_workflow_tools.core.gerrit.change_view import ChangeView
from gerrit_workflow_tools.core.reviewer import ReviewerAccount


def _view(**payload: Any) -> ChangeView:
    return ChangeView.from_payload(payload)


@pytest.mark.parametrize(
    "label, expected",
    [
        ({"value": 1, "all": [{"value": 1}]}, 1),
        ({"all": [{"value": 2}, {"value": -2}]}, -2),  # a blocking vote outranks approvals
        ({"all": [{"value": 1}, {"value": 2}]}, 2),
        ({"value": 0, "all": [{"value": 0}]}, None),  # nobody voted
        ({"all": []}, None),
        ("garbage", None),
    ],
)
def test_label_value(label: Any, expected: int | None) -> None:
    assert _view(labels={"Code-Review": label}).code_review == expected


def test_missing_reviewers_field_differs_from_an_empty_list() -> None:
    assert _view().reviewers is None
    assert _view(reviewers={}).reviewers == ()
    assert _view(reviewers={"REVIEWER": [{"username": "al", "_account_id": 7}]}).reviewers == (
        ReviewerAccount(slug="al", account_id=7),
    )


def test_unresolved_count_absent_is_none() -> None:
    assert _view().unresolved_comments is None
    assert _view(unresolved_comment_count=3).unresolved_comments == 3


def test_revisions_are_keyed_case_insensitively() -> None:
    change = _view(
        current_revision="ABC",
        revisions={"Abc": {"_number": 4, "commit": {"parents": [{"commit": "PARENT"}]}}},
    )
    assert change.current_revision == "abc"
    assert change.current_revision_number == 4
    assert change.first_parent == "parent"


def test_first_parent_accepts_a_bare_sha() -> None:
    change = _view(current_revision="c", revisions={"c": {"commit": {"parents": ["P1", "P2"]}}})
    assert change.first_parent == "p1"


def test_current_revision_created_falls_back_through_commit_and_change() -> None:
    created = "2026-08-01 10:00:00.000000000"
    committer = {"date": "2026-08-02 10:00:00.000000000"}
    assert _view(updated="2026-08-03 10:00:00.000000000").current_revision_created() == datetime(
        2026, 8, 3, 10, tzinfo=timezone.utc
    )
    no_patch = _view(created=created, current_revision="x", revisions={})
    assert no_patch.current_revision_created() == datetime(2026, 8, 1, 10, tzinfo=timezone.utc)
    by_commit = _view(created=created, current_revision="x", revisions={"x": {"commit": {"committer": committer}}})
    assert by_commit.current_revision_created() == datetime(2026, 8, 2, 10, tzinfo=timezone.utc)


def test_attention_set_by_key_and_by_account() -> None:
    change = _view(
        attention_set={
            "7": {"last_update": "2026-08-01 10:00:00.000000000"},
            "x": {"account": {"_account_id": 8}, "last_update": "2026-08-02 10:00:00.000000000"},
        }
    )
    assert change.attention_set[7] == datetime(2026, 8, 1, 10, tzinfo=timezone.utc)
    assert change.attention_set[8] == datetime(2026, 8, 2, 10, tzinfo=timezone.utc)


def test_owner_display_prefers_the_full_name() -> None:
    change = _view(owner={"name": "Al Ice", "username": "al", "email": " al@x.org "})
    assert change.owner_name == "Al Ice"
    assert change.owner_email == "al@x.org"
    assert _view().owner_name == "?"


def test_url_and_key() -> None:
    change = _view(id="tools/x~main~I1", project="tools/x", _number=12)
    assert change.key == "tools/x~main~I1"
    assert change.url("https://g") == "https://g/c/tools%2Fx/+/12"
    assert _view(_number=5).key == "5"
    assert _view(_number=5).url("https://g") is None


@pytest.mark.parametrize("status, is_open", [(None, True), ("", True), ("new", True), ("MERGED", False)])
def test_is_open(status: str | None, is_open: bool) -> None:
    assert _view(status=status).is_open is is_open
