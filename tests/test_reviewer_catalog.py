from __future__ import annotations

from gerrit_workflow_tools.push_input_line import PushLineState
from gerrit_workflow_tools.push_input_prompt import _bottom_toolbar
from gerrit_workflow_tools.reviewer_catalog import ReviewerCatalog


class _FakeClient:
    def __init__(self) -> None:
        self.account_queries: list[str] = []

    def query_accounts(self, query: str, *, n: int = 10) -> list[dict[str, object]]:
        self.account_queries.append(query)
        if query == "username:alice":
            return [{"username": "alice"}]
        if query == "username:unknown":
            return []
        if query == "username:dup":
            return [{"username": "dup-a"}, {"username": "dup-b"}]
        return []

    def suggest_change_reviewers(
        self, change_id: str, *, query: str | None = None, n: int = 20
    ) -> list[dict[str, object]]:
        del change_id, query, n
        return [{"account": {"username": "seed-from-change"}}]

    def get_plugin_project_reviewers(self, project: str) -> list[dict[str, object]] | None:
        del project
        return [{"username": "seed-from-plugin"}]


def test_catalog_completion_candidates_dedupe() -> None:
    c = ReviewerCatalog(client=None, candidates=["alice", "Alice", "bob"])
    assert c.completion_candidates() == ["alice", "bob"]


def test_catalog_validate_state_marks_unknown_and_ambiguous() -> None:
    c = ReviewerCatalog(client=_FakeClient(), candidates=[])
    s = PushLineState(reviewers=["unknown", "dup"])

    first = c.validate_state(s)
    assert first.pending_checks is True
    assert [i.reviewer for i in first.issues] == ["unknown"]

    c._next_allowed_query_at = 0.0
    second = c.validate_state(s)
    assert second.pending_checks is False
    assert [i.reviewer for i in second.issues] == ["unknown", "dup"]


def test_bottom_toolbar_shows_red_for_gerrit_reviewer_issue() -> None:
    c = ReviewerCatalog(client=None, candidates=[])
    c._validation_cache["bad"] = "unknown"
    toolbar = _bottom_toolbar("bad", c)
    assert toolbar is not None
    assert ("fg:ansired", "reviewer: Gerrit could not resolve reviewer `bad`.") in list(toolbar)
