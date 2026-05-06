from __future__ import annotations

from gerrit_workflow_tools.core.reviewer_completion import (
    slug_from_suggest_or_account_row,
    sorted_slugs_from_account_rows,
)


def test_slug_prefers_nested_account() -> None:
    row = {"account": {"username": "nested"}, "username": "ignored"}
    assert slug_from_suggest_or_account_row(row) == "nested"


def test_sorted_slugs_optional_prefix_filter() -> None:
    rows = [
        {"username": "baa"},
        {"username": "bal"},
        {"username": "bez"},
    ]
    assert sorted_slugs_from_account_rows(rows, must_start_with=None) == ["baa", "bal", "bez"]
    assert sorted_slugs_from_account_rows(rows, must_start_with="ba") == ["baa", "bal"]
