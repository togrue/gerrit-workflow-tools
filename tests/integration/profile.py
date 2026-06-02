"""Optional integration-test timing report (set ``GERRIT_IT_PROFILE=1``)."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager

_phases: list[tuple[str, float]] = []


def profiling_enabled() -> bool:
    return os.environ.get("GERRIT_IT_PROFILE", "").lower() in ("1", "true", "yes")


@contextmanager
def phase(name: str) -> Iterator[None]:
    if not profiling_enabled():
        yield
        return
    t0 = time.monotonic()
    try:
        yield
    finally:
        _phases.append((name, time.monotonic() - t0))


def record(name: str, seconds: float) -> None:
    if profiling_enabled():
        _phases.append((name, seconds))


def reset() -> None:
    _phases.clear()


def format_report() -> str:
    if not _phases:
        return ""
    lines = ["", "=== GERRIT_IT_PROFILE ==="]
    buckets: dict[str, float] = {}
    for name, sec in _phases:
        if name.startswith("docker:"):
            key = "session: docker"
        elif name.startswith("seed:"):
            key = "session: seed"
        elif name.startswith("prepare_topic_repo("):
            key = "per-test: prepare_topic_repo (also inside test: times below)"
        elif name.startswith("test:"):
            key = "per-test: body (includes prepare_topic_repo)"
        else:
            key = "other"
        buckets[key] = buckets.get(key, 0.0) + sec
        lines.append(f"  {sec:7.2f}s  {name}")
    lines.append(f"  {'—' * 7}  —")
    lines.append("  Summary (prepare_topic_repo overlaps test: lines):")
    for key in (
        "session: docker",
        "session: seed",
        "per-test: prepare_topic_repo (also inside test: times below)",
        "per-test: body (includes prepare_topic_repo)",
        "other",
    ):
        if key in buckets:
            lines.append(f"    {buckets[key]:7.2f}s  {key}")
    lines.append("=" * 48)
    return "\n".join(lines)
