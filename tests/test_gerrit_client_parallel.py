"""Tests for Gerrit HTTP parallelism helpers."""

from __future__ import annotations

from gerrit_workflow_tools.core.gerrit.rest import parallel_map


def test_parallel_map_preserves_input_order() -> None:
    def fn(value: int) -> int:
        return value * 10

    jobs = [lambda v=value: fn(v) for value in [1, 2, 3, 4, 5]]
    assert parallel_map(jobs) == [10, 20, 30, 40, 50]


def test_parallel_map_allows_none_results() -> None:
    def fn(value: int) -> int | None:
        return None if value % 2 == 0 else value

    jobs = [lambda v=value: fn(v) for value in [1, 2, 3]]
    assert parallel_map(jobs) == [1, None, 3]
