"""Tests for parsing Gerrit list-reviewers REST payloads."""

from __future__ import annotations

from gerrit_workflow_tools.core.gerrit_change_status import ReviewerAccount
from gerrit_workflow_tools.core.reviewer import reviewer_accounts_from_reviewer_list


def test_reviewer_accounts_from_flat_reviewer_list() -> None:
    rows = [
        {"_account_id": 1, "username": "alice", "email": "alice@example.com"},
        {"_account_id": 2, "username": "bob", "email": "bob@example.com"},
    ]
    out = reviewer_accounts_from_reviewer_list(rows)
    assert out == [
        ReviewerAccount(slug="alice", account_id=1),
        ReviewerAccount(slug="bob", account_id=2),
    ]


def test_reviewer_accounts_from_reviewer_info_list() -> None:
    rows = [
        {"state": "REVIEWER", "account": {"_account_id": 3, "username": "carol"}},
        {"state": "CC", "account": {"_account_id": 4, "name": "Dave"}},
    ]
    out = reviewer_accounts_from_reviewer_list(rows)
    assert len(out) == 2
    assert out[0].slug == "carol"
    assert out[1].slug == "Dave"
