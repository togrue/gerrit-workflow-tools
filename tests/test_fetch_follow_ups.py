"""Tests for isolated Gerrit follow-up fetches in :func:`fetch_gerrit_data`."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from gerrit_workflow_tools.core.gerrit_change_status import (
    ReviewerAccount,
    _execute_follow_ups,
    _FollowUpWork,
)


def test_execute_follow_ups_continues_after_one_kind_fails() -> None:
    client = MagicMock()
    work = _FollowUpWork(2, "proj~main~Iabc", frozenset({"comments", "reviewers"}))

    with (
        patch(
            "gerrit_workflow_tools.core.gerrit_change_status.count_unresolved_via_comments",
            side_effect=RuntimeError("comments boom"),
        ),
        patch(
            "gerrit_workflow_tools.core.gerrit_change_status._load_reviewers_for_change",
            return_value=[ReviewerAccount(slug="alice")],
        ),
    ):
        idx, updates = _execute_follow_ups(client, work)

    assert idx == 2
    assert "comments" not in updates
    assert updates["reviewers"] == [ReviewerAccount(slug="alice")]
