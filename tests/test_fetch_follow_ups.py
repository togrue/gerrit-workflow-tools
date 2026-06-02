"""Tests for follow-up fetch resilience in :meth:`GerritService.fetch_gerrit_data`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.gerrit_change_status import ReviewerAccount


@dataclass
class _FakeRow:
    sha: str = "aabbcc"
    short_sha: str = "aabbcc"
    summary: str = "feat: thing"
    change_id: str | None = "Iabc123"


# Detail missing ``unresolved_comment_count`` and ``reviewers`` — triggers
# both follow-up kinds.  Verified=0 so no checks follow-up.
_DETAIL: dict[str, Any] = {
    "change_id": "Iabc123",
    "status": "NEW",
    "subject": "feat: thing",
    "labels": {"Verified": {"value": 0}, "Code-Review": {"value": 0}},
    "_number": 1,
    "project": "proj",
    "branch": "main",
}


def _make_service(detail: dict[str, Any] | None = None) -> GerritService:
    rest = MagicMock()
    rest.web_base = "https://gerrit.example.com"
    cache = MagicMock()
    cache.load_changes.return_value = {"Iabc123": detail} if detail is not None else {}
    return GerritService(rest, cache)


def test_fetch_gerrit_data_continues_when_comments_follow_up_fails() -> None:
    """A comments fetch failure must not prevent reviewers from being populated."""

    service = _make_service(_DETAIL)
    alice = ReviewerAccount(slug="alice", account_id=42)

    with (
        patch.object(service.comments, "get_file_map", side_effect=RuntimeError("boom")),
        patch.object(
            service.rest,
            "list_change_reviewers",
            return_value=[{"account": {"_account_id": 42, "username": "alice"}, "state": "REVIEWER"}],
        ),
    ):
        commits = service.fetch_gerrit_data([_FakeRow()])

    assert len(commits) == 1
    lc = commits[0]
    # Comments failed — field stays at the default value (0), no exception raised.
    assert lc.comments_unresolved == 0
    # Reviewers succeeded despite the comments failure.
    assert lc.reviewers == [alice]
