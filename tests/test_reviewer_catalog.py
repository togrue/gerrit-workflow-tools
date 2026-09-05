from __future__ import annotations

import threading
import time
from typing import Any

from gerrit_workflow_tools.push_input_line import PushLineState
from gerrit_workflow_tools.push_input_prompt import _bottom_toolbar
from gerrit_workflow_tools.reviewer_catalog import _REQUEST_INTERVAL_SECONDS, ReviewerCatalog


class _FakeClient:
    def __init__(self) -> None:
        self.account_queries: list[str] = []
        self.suggest_queries: list[str | None] = []
        self.plugin_calls: list[str] = []
        self._lock = threading.Lock()

    def query_accounts(self, query: str, *, n: int = 10) -> list[dict[str, Any]]:
        with self._lock:
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
    ) -> list[dict[str, Any]]:
        del change_id, n
        with self._lock:
            self.suggest_queries.append(query)
        if query and query.lower() == "ben":
            return [{"account": {"username": "ben"}}]
        if not query:
            return [{"account": {"username": "seed-from-change"}}]
        return []

    def get_plugin_project_reviewers(self, project: str) -> list[dict[str, Any]] | None:
        with self._lock:
            self.plugin_calls.append(project)
        return [{"username": "seed-from-plugin"}]


def test_catalog_completion_candidates_dedupe() -> None:
    c = ReviewerCatalog(client=None, candidates=["alice", "Alice", "bob"])
    assert c.completion_candidates() == ["alice", "bob"]


def test_complete_prefix_uses_suggest_query_with_change_id() -> None:
    client = _FakeClient()
    c = ReviewerCatalog(client=client, candidates=[], change_id_hint="id/123")
    try:
        assert c.complete_prefix("ben") == []
        assert c.wait_until_idle(timeout=2.0)
        assert c.complete_prefix("ben") == ["ben"]
        assert client.suggest_queries[-1] == "ben"
        assert c.complete_prefix("ben") == ["ben"]
        assert client.suggest_queries.count("ben") == 1
    finally:
        c.close()


def test_complete_prefix_falls_back_to_account_query_without_change() -> None:
    client = _FakeClient()
    c = ReviewerCatalog(client=client, candidates=[], change_id_hint=None)
    try:
        assert c.complete_prefix("ben") == []
        assert c.wait_until_idle(timeout=2.0)
        assert c.complete_prefix("ben") == ["ben"]
        assert "username:ben*" in client.account_queries
    finally:
        c.close()


def test_complete_prefix_does_not_block_on_rest() -> None:
    started = threading.Event()
    release = threading.Event()

    class SlowClient(_FakeClient):
        def suggest_change_reviewers(
            self, change_id: str, *, query: str | None = None, n: int = 20
        ) -> list[dict[str, Any]]:
            started.set()
            assert release.wait(timeout=2.0)
            return super().suggest_change_reviewers(change_id, query=query, n=n)

    c = ReviewerCatalog(client=SlowClient(), candidates=[], change_id_hint="id/123")
    try:
        t0 = time.monotonic()
        assert c.complete_prefix("ben") == []
        assert time.monotonic() - t0 < 0.2
        assert started.wait(timeout=2.0)
        release.set()
        assert c.wait_until_idle(timeout=2.0)
        assert c.complete_prefix("ben") == ["ben"]
    finally:
        release.set()
        c.close()


def test_catalog_validate_state_marks_unknown_and_ambiguous() -> None:
    c = ReviewerCatalog(client=_FakeClient(), candidates=[])
    try:
        s = PushLineState(reviewers=["unknown", "dup"])

        first = c.validate_state(s)
        assert first.pending_checks is True
        assert first.issues == []
        assert c.wait_until_idle(timeout=2.0)

        mid = c.validate_state(s)
        assert [i.reviewer for i in mid.issues] == ["unknown"]
        assert mid.pending_checks is True
        assert c.wait_until_idle(timeout=2.0)

        second = c.validate_state(s)
        assert second.pending_checks is False
        assert [i.reviewer for i in second.issues] == ["unknown", "dup"]
    finally:
        c.close()


def test_shared_rate_limit_spaces_requests() -> None:
    starts: list[float] = []
    lock = threading.Lock()

    class TimingClient(_FakeClient):
        def suggest_change_reviewers(
            self, change_id: str, *, query: str | None = None, n: int = 20
        ) -> list[dict[str, Any]]:
            with lock:
                starts.append(time.monotonic())
            return super().suggest_change_reviewers(change_id, query=query, n=n)

        def query_accounts(self, query: str, *, n: int = 10) -> list[dict[str, Any]]:
            with lock:
                starts.append(time.monotonic())
            return super().query_accounts(query, n=n)

    c = ReviewerCatalog(client=TimingClient(), candidates=[], change_id_hint="id/1")
    try:
        assert c.complete_prefix("ben") == []
        assert c.complete_prefix("ali") == []
        assert c.wait_until_idle(timeout=3.0)
        assert len(starts) >= 2
        assert starts[1] - starts[0] >= _REQUEST_INTERVAL_SECONDS - 0.05
    finally:
        c.close()


def test_stale_complete_generation_is_ignored() -> None:
    client = _FakeClient()
    c = ReviewerCatalog(client=client, candidates=[], change_id_hint="id/1", start_worker=False)
    try:
        from gerrit_workflow_tools.reviewer_catalog import _Job

        with c._lock:
            c._complete_generation["ben"] = 2
            c._complete_tokens["ben"] = "ben"
        # An older in-flight generation must not populate the cache.
        c._run_complete(client, _Job(kind="complete", key="ben", generation=1, token="ben"))
        assert "ben" not in c._prefix_completion_cache
        # Current generation still applies.
        c._run_complete(client, _Job(kind="complete", key="ben", generation=2, token="ben"))
        assert c._prefix_completion_cache["ben"] == ["ben"]
    finally:
        c.close()


def test_bottom_toolbar_shows_red_for_gerrit_reviewer_issue() -> None:
    c = ReviewerCatalog(client=None, candidates=[])
    c._validation_cache["bad"] = "unknown"
    toolbar = _bottom_toolbar("bad", c)
    assert toolbar is not None
    joined = "".join(fragment[1] for fragment in toolbar)
    assert "reviewer: Gerrit could not resolve reviewer `bad`." in joined


def test_bottom_toolbar_stays_silent_while_pending() -> None:
    c = ReviewerCatalog(client=None, candidates=[])
    toolbar = _bottom_toolbar("alice", c)
    assert toolbar is not None
    text = "".join(fragment[1] for fragment in toolbar)
    assert "checking Gerrit" not in text
    assert "keywords:" in text
