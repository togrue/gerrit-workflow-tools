"""Regression tests for batch Gerrit change loading performance and query shape."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from gerrit_workflow_tools.core.gerrit.cache import GerritCache
from gerrit_workflow_tools.core.gerrit.change_resolution import StackContext
from gerrit_workflow_tools.core.gerrit.rest import (
    _BATCH_OR_CHUNK,
    alias_batch_fetch_results,
    batch_load_change_details,
)
from gerrit_workflow_tools.core.gerrit.service import GerritService
from tests.change_store import ChangeStore


def _change_id(n: int) -> str:
    return f"I{format(n, '040x')}"


def _stack(project: str = "test-git-graph-repo") -> StackContext:
    return StackContext(project=project, target_branch="dev", push_branch="dev")


def test_get_payloads_resolves_stack_context_once(tmp_path: Path) -> None:
    """Batch change loading must not call resolve_stack_context per Change-Id."""
    calls = 0

    def counting_resolve(cwd: Path | str | None, branch: str | None = None) -> StackContext:
        del cwd, branch
        nonlocal calls
        calls += 1
        return _stack()

    rest = MagicMock()
    cache = MagicMock()
    cache.load_changes.return_value = {}

    service = GerritService(rest, cache, cwd=tmp_path)
    change_ids = [_change_id(i) for i in range(50)]

    with patch(
        "gerrit_workflow_tools.core.gerrit.service.resolve_stack_context",
        side_effect=counting_resolve,
    ):
        service.changes.get_payloads(change_ids)

    assert calls == 1
    triplets = cache.load_changes.call_args[0][0]
    assert len(triplets) == 50
    assert all(t.startswith("test-git-graph-repo~dev~") for t in triplets)


def test_batch_load_uses_gerrit_project_not_scp_port_prefix() -> None:
    """Triplet queries must use the Gerrit project name, not host:port/path from scp remotes."""
    cid = _change_id(1)
    triplet = f"test-git-graph-repo~dev~{cid}"
    row = {
        "id": triplet,
        "change_id": cid,
        "project": "test-git-graph-repo",
        "branch": "dev",
        "_number": 1,
    }
    queries: list[str] = []
    client = MagicMock()

    def query_changes(q: str, n: int, options: list[str] | None = None) -> list[dict[str, Any]]:
        del n, options
        queries.append(q)
        if f"project:test-git-graph-repo change:{cid}" in q:
            return [row]
        return []

    client.query_changes.side_effect = query_changes

    out = batch_load_change_details(client, [triplet])

    assert out[triplet] == row
    assert queries
    assert "project:29418/" not in queries[0]
    assert "branch:" not in queries[0]
    assert f"project:test-git-graph-repo change:{cid}" in queries[0]


def test_batch_load_many_changes_issues_one_query_per_chunk() -> None:
    """Regression: stack overlay must batch OR-query changes, not query each one."""
    n = _BATCH_OR_CHUNK + 5
    refs = [f"test-git-graph-repo~dev~{_change_id(i)}" for i in range(n)]
    row = {
        "id": refs[0],
        "change_id": refs[0].split("~")[-1],
        "project": "test-git-graph-repo",
        "branch": "dev",
        "_number": 1,
    }
    client = MagicMock()

    def query_changes(q: str, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        del args, kwargs
        if q.startswith("project:test-git-graph-repo (change:"):
            return [row]
        return []

    client.query_changes.side_effect = query_changes

    batch_load_change_details(client, refs)

    assert client.query_changes.call_count == 2


def test_fetch_payloads_aliases_target_branch_only(tmp_path: Path) -> None:
    """Other-branch rows are cached but only the target-branch triplet is returned for lookup."""
    cid = _change_id(1)
    target = f"test-git-graph-repo~dev~{cid}"
    other = f"test-git-graph-repo~main~{cid}"
    row_dev = {
        "id": "test-git-graph-repo~10",
        "change_id": cid,
        "project": "test-git-graph-repo",
        "branch": "dev",
        "_number": 10,
        "updated": "2026-01-01 00:00:00.000000000",
    }
    row_main = {
        "id": "test-git-graph-repo~11",
        "change_id": cid,
        "project": "test-git-graph-repo",
        "branch": "main",
        "_number": 11,
        "updated": "2026-01-01 00:00:00.000000000",
    }

    rest = MagicMock()

    def query_changes(q: str, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        del args, kwargs
        assert "branch:" not in q
        return [row_main, row_dev]

    rest.query_changes.side_effect = query_changes

    cache = MagicMock()
    stored: dict[str, dict[str, Any]] = {}

    def load_changes(triplets, **kwargs):
        del kwargs
        fetched = batch_load_change_details(rest, triplets)
        aliased = alias_batch_fetch_results(triplets, fetched)
        stored.update(aliased)
        return {t: aliased[t] for t in triplets if t in aliased}

    cache.load_changes.side_effect = load_changes

    service = GerritService(rest, cache, cwd=tmp_path)
    with patch(
        "gerrit_workflow_tools.core.gerrit.service.resolve_stack_context",
        return_value=_stack(),
    ):
        payloads = service.changes.get_payloads([cid])

    assert payloads[target]["_number"] == 10
    assert other not in payloads
    assert stored[row_main["id"]]["_number"] == 11
    assert stored[target]["_number"] == 10


def test_find_by_footer_change_ids_returns_every_branch_after_an_overlay_fetch(tmp_path: Path) -> None:
    """The multi-branch note source: one batch fetch, then a local lookup across branches."""
    cid = _change_id(7)
    rows = {
        f"proj~main~{cid}": {
            "id": f"proj~main~{cid}",
            "change_id": cid,
            "project": "proj",
            "branch": "main",
            "_number": 1,
            "updated": "2026-01-01 00:00:00.000000000",
        },
        f"proj~dev~{cid}": {
            "id": f"proj~dev~{cid}",
            "change_id": cid,
            "project": "proj",
            "branch": "dev",
            "_number": 2,
            "updated": "2026-01-01 00:00:00.000000000",
        },
    }
    store = ChangeStore(rows)
    service = GerritService(store, GerritCache(tmp_path / "cache.db", web_base=store.web_base), cwd=tmp_path)

    with patch(
        "gerrit_workflow_tools.core.gerrit.service.resolve_stack_context",
        return_value=StackContext(project="proj", target_branch="main", push_branch="main"),
    ):
        # Cold: nothing stored yet, so the lookup finds nothing rather than fetching.
        assert service.changes.find_by_footer_change_ids([cid]) == {cid: []}
        assert store.queries() == []

        service.changes.get_payloads([cid])
        found = service.changes.find_by_footer_change_ids([cid])

    assert sorted(row["branch"] for row in found[cid]) == ["dev", "main"]
    # The lookup itself is local: the only query issued was the overlay's batch fetch.
    assert len(store.queries()) == 1
