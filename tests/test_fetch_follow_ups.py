"""Tests for follow-up fetch resilience in :meth:`GerritService.fetch_gerrit_data`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

from gerrit_workflow_tools.core.gerrit.change_resolution import StackContext
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
    "id": "proj~main~Iabc123",
    "change_id": "Iabc123",
    "status": "NEW",
    "subject": "feat: thing",
    "labels": {"Verified": {"value": 0}, "Code-Review": {"value": 0}},
    "_number": 1,
    "project": "proj",
    "branch": "main",
}

_STACK = StackContext(project="proj", target_branch="origin/main", push_branch="main")


def _make_service(detail: dict[str, Any] | None = None) -> GerritService:
    rest = MagicMock()
    rest.web_base = "https://gerrit.example.com"
    cache = MagicMock()
    cache.load_changes.return_value = {"Iabc123": detail} if detail is not None else {}
    cache.capability.return_value = None  # host capability not yet discovered
    return GerritService(rest, cache)


def test_fetch_gerrit_data_continues_when_comments_follow_up_fails() -> None:
    """A comments fetch failure must not prevent reviewers from being populated."""

    service = _make_service(_DETAIL)
    alice = ReviewerAccount(slug="alice", account_id=42)

    with (
        patch(
            "gerrit_workflow_tools.core.gerrit.service.resolve_stack_context",
            return_value=_STACK,
        ),
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


def test_reviewers_follow_up_skipped_when_payload_carries_an_empty_reviewer_list() -> None:
    """An empty ``reviewers`` map is an answer, not a hole — no /reviewers/ GET."""

    detail = {**_DETAIL, "reviewers": {}, "unresolved_comment_count": 0}
    service = _make_service(detail)

    with patch(
        "gerrit_workflow_tools.core.gerrit.service.resolve_stack_context",
        return_value=_STACK,
    ):
        commits = service.fetch_gerrit_data([_FakeRow()])

    service.rest.list_change_reviewers.assert_not_called()
    assert commits[0].reviewers == []


def test_reviewers_follow_up_runs_when_payload_omits_the_field() -> None:
    """Payloads fetched without DETAILED_LABELS carry no ``reviewers`` — still follow up."""

    detail = {**_DETAIL, "unresolved_comment_count": 0}
    assert "reviewers" not in detail
    service = _make_service(detail)

    with (
        patch(
            "gerrit_workflow_tools.core.gerrit.service.resolve_stack_context",
            return_value=_STACK,
        ),
        patch.object(
            service.rest,
            "list_change_reviewers",
            return_value=[{"account": {"_account_id": 42, "username": "alice"}, "state": "REVIEWER"}],
        ) as list_reviewers,
    ):
        commits = service.fetch_gerrit_data([_FakeRow()])

    list_reviewers.assert_called_once()
    assert commits[0].reviewers == [ReviewerAccount(slug="alice", account_id=42)]


def test_comments_follow_up_passes_change_updated_as_the_cache_key() -> None:
    """`LogCommit.updated` reaches `load_comments`, so its validity rule can fire."""

    detail = {**_DETAIL, "reviewers": {}, "updated": "2026-06-03 00:31:56.000000000"}
    service = _make_service(detail)

    with (
        patch(
            "gerrit_workflow_tools.core.gerrit.service.resolve_stack_context",
            return_value=_STACK,
        ),
        patch.object(service.comments, "get_file_map", return_value={}) as get_file_map,
    ):
        commits = service.fetch_gerrit_data([_FakeRow()])

    assert commits[0].updated == "2026-06-03 00:31:56.000000000"
    get_file_map.assert_called_once_with(
        "proj~main~Iabc123",
        change_updated="2026-06-03 00:31:56.000000000",
    )


def test_checks_endpoint_is_probed_once_per_host_not_once_per_change() -> None:
    """A 404 means the host has no Checks plugin — stop asking for the rest of the run."""

    from gerrit_workflow_tools.core.gerrit.rest import GerritApiError

    service = _make_service()
    service.rest.get_checks.side_effect = GerritApiError("nope", status=404)

    assert service._checks_or_empty("proj~main~I1") == []
    assert service._checks_or_empty("proj~main~I2") == []
    assert service._checks_or_empty("proj~main~I3") == []

    service.rest.get_checks.assert_called_once()


def test_a_later_run_skips_checks_entirely_once_the_host_said_404() -> None:
    """The verdict is cached, so run 2 makes no /checks call at all.

    An in-run memo cannot achieve this: `ger log`'s follow-ups run concurrently, so they
    all ask before the first 404 comes back. Only the persisted verdict removes the calls.
    """

    from gerrit_workflow_tools.core.gerrit.rest import GerritApiError

    first = _make_service()
    first.rest.get_checks.side_effect = GerritApiError("nope", status=404)
    first._checks_or_empty("proj~main~I1")
    first.cache.set_capability.assert_called_once_with("checks", False)

    second = _make_service()
    second.cache.capability.return_value = False  # what run 1 persisted

    assert second._checks_or_empty("proj~main~I1") == []
    second.rest.get_checks.assert_not_called()


def test_a_non_404_checks_error_stays_per_change() -> None:
    """A transient failure on one change must not disable checks for the others."""

    from gerrit_workflow_tools.core.gerrit.rest import GerritApiError

    service = _make_service()
    service.rest.get_checks.side_effect = [GerritApiError("boom", status=500), [{"state": "FAILED"}]]

    assert service._checks_or_empty("proj~main~I1") == []
    assert service._checks_or_empty("proj~main~I2") == [{"state": "FAILED"}]
