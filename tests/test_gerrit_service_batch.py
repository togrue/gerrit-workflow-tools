"""Regression tests for batch Gerrit change loading performance and query shape."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from gerrit_workflow_tools.core.gerrit.change_resolution import StackContext
from gerrit_workflow_tools.core.gerrit.rest import _BATCH_OR_CHUNK, batch_load_change_details
from gerrit_workflow_tools.core.gerrit.service import GerritService


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
        if f"project:test-git-graph-repo branch:dev change:{cid}" in q:
            return [row]
        return []

    client.query_changes.side_effect = query_changes

    out = batch_load_change_details(client, [triplet])

    assert out[triplet] == row
    assert queries
    assert "project:29418/" not in queries[0]
    assert f"project:test-git-graph-repo branch:dev change:{cid}" in queries[0]


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
        if q.startswith("project:test-git-graph-repo branch:dev (change:"):
            return [row]
        return []

    client.query_changes.side_effect = query_changes

    batch_load_change_details(client, refs)

    assert client.query_changes.call_count == 2
