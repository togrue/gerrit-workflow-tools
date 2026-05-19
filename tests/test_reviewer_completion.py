from __future__ import annotations

from gerrit_workflow_tools.core.reviewer import format_gerrit_account_label
from gerrit_workflow_tools.core.reviewer_completion import (
    slug_from_suggest_or_account_row,
    sorted_slugs_from_account_rows,
)


def test_format_gerrit_account_label_username_and_name() -> None:
    assert format_gerrit_account_label({"username": "grt", "name": "Tobias Grün"}) == "grt (Tobias Grün)"


def test_format_gerrit_account_label_slug_only() -> None:
    assert format_gerrit_account_label({"username": "alice"}) == "alice"


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
