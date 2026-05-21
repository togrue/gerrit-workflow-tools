"""Tests for Gerrit HTTP parallelism helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

from gerrit_workflow_tools.core.gerrit_client import GerritClient, parallel_io_workers, parallel_map, partition_tasks


def test_parallel_io_workers_scales_down_for_small_batches() -> None:
    assert parallel_io_workers(1) == 1
    assert parallel_io_workers(3, min_tasks_per_worker=2) == 2
    assert parallel_io_workers(10, min_tasks_per_worker=2) == 5
    assert parallel_io_workers(100, min_tasks_per_worker=1) == 8


def test_partition_tasks_spreads_evenly() -> None:
    assert partition_tasks([], 4) == []
    assert partition_tasks(["a"], 4) == [["a"]]
    assert partition_tasks(["a", "b", "c", "d", "e"], 2) == [["a", "c", "e"], ["b", "d"]]


def test_parallel_map_preserves_input_order() -> None:
    client = MagicMock(spec=GerritClient)

    def fn(_client: GerritClient, value: int) -> int:
        return value * 10

    assert parallel_map(client, [1, 2, 3, 4, 5], fn, min_tasks_per_worker=1) == [10, 20, 30, 40, 50]


def test_parallel_map_allows_none_results() -> None:
    client = MagicMock(spec=GerritClient)

    def fn(_client: GerritClient, value: int) -> int | None:
        return None if value % 2 == 0 else value

    assert parallel_map(client, [1, 2, 3], fn) == [1, None, 3]
