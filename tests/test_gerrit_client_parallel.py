"""Tests for Gerrit HTTP parallelism helpers."""

from __future__ import annotations

from gerrit_workflow_tools.core.gerrit_client import parallel_io_workers, partition_tasks


def test_parallel_io_workers_scales_down_for_small_batches() -> None:
    assert parallel_io_workers(1) == 1
    assert parallel_io_workers(3, min_tasks_per_worker=2) == 2
    assert parallel_io_workers(10, min_tasks_per_worker=2) == 5
    assert parallel_io_workers(100, min_tasks_per_worker=1) == 8


def test_partition_tasks_spreads_evenly() -> None:
    assert partition_tasks([], 4) == []
    assert partition_tasks(["a"], 4) == [["a"]]
    assert partition_tasks(["a", "b", "c", "d", "e"], 2) == [["a", "c", "e"], ["b", "d"]]
