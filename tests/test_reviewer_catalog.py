from __future__ import annotations

from gerrit_workflow_tools.push_input_line import PushLineState
from gerrit_workflow_tools.push_input_prompt import _bottom_toolbar
from gerrit_workflow_tools.reviewer_catalog import ReviewerCatalog


class _FakeClient:
    def __init__(self) -> None:
        self.account_queries: list[str] = []
        self.suggest_queries: list[str | None] = []

    def query_accounts(self, query: str, *, n: int = 10) -> list[dict[str, object]]:
        self.account_queries.append(query)
        if query == "username:alice":
            return [{"username": "alice"}]
        if query == "username:unknown":
            return []
        if query == "username:dup":
            return [{"username": "dup-a"}, {"username": "dup-b"}]
        if query == "username:ben*":
            return [{"username": "ben"}]
        return []

    def suggest_change_reviewers(
        self, change_id: str, *, query: str | None = None, n: int = 20
    ) -> list[dict[str, object]]:
        del change_id, n
        self.suggest_queries.append(query)
        if query and query.lower() == "ben":
            return [{"account": {"username": "ben"}}]
        if not query:
            return [{"account": {"username": "seed-from-change"}}]
        return []

    def get_plugin_project_reviewers(self, project: str) -> list[dict[str, object]] | None:
        del project
        return [{"username": "seed-from-plugin"}]


def test_catalog_completion_candidates_dedupe() -> None:
    c = ReviewerCatalog(client=None, candidates=["alice", "Alice", "bob"])
    assert c.completion_candidates() == ["alice", "bob"]


def test_complete_prefix_uses_suggest_query_with_change_id() -> None:
    client = _FakeClient()
    c = ReviewerCatalog(client=client, candidates=[], change_id_hint="id/123")
    assert c.complete_prefix("ben") == ["ben"]
    assert client.suggest_queries[-1] == "ben"
    # cached
    assert c.complete_prefix("ben") == ["ben"]
    assert client.suggest_queries.count("ben") == 1


def test_complete_prefix_falls_back_to_account_query_without_change() -> None:
    client = _FakeClient()
    c = ReviewerCatalog(client=client, candidates=[], change_id_hint=None)
    assert c.complete_prefix("ben") == ["ben"]
    assert "username:ben*" in client.account_queries


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
