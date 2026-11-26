"""High-level git/Gerrit call timing: for ``--debug-log``, and per ``ger serve`` request.

Two sinks, one recording point (:func:`record_call`): the process-wide list ``--debug-log``
prints at the end of a command, and the list :func:`collect_calls` opens for one piece of work
(a ``ger serve`` request), which sees only the calls made in its own context: a background
thread's calls stay out of it.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse


_ENABLED = False
_records: list[tuple[str, str, float]] = []
_collected: ContextVar[list[tuple[str, str, float]] | None] = ContextVar("ger_collected_calls", default=None)
_CHANGE_PATH_RE = re.compile(r"^(GET|POST|PUT|DELETE) changes/[^/]+/")


@dataclass(frozen=True)
class CallTraceEntry:
    """One traced subprocess or HTTP round trip."""

    kind: str
    label: str
    seconds: float


def set_call_trace_enabled(enabled: bool) -> None:
    """Turn request tracing on or off for the current CLI run."""

    global _ENABLED  # pylint: disable=global-statement
    _ENABLED = enabled
    if not enabled:
        reset_call_trace()


def reset_call_trace() -> None:
    """Drop collected records (mainly for tests)."""

    _records.clear()


def record_call(kind: str, label: str, seconds: float) -> None:
    """Record one traced call: into the collection open in this context, and when ``--debug-log`` traces."""

    collected = _collected.get()
    if collected is not None:
        collected.append((kind, label, seconds))
    if _ENABLED:
        _records.append((kind, label, seconds))


@contextmanager
def collect_calls() -> Iterator[list[CallTraceEntry]]:
    """Collect the calls made in this context into the list yielded, filled when the block ends.

    Threads started with a copy of the context (``rest.parallel_map``) record into it too.
    Costs one context lookup per call; nothing is recorded outside the block.
    """
    raw: list[tuple[str, str, float]] = []
    entries: list[CallTraceEntry] = []
    token = _collected.set(raw)
    try:
        yield entries
    finally:
        _collected.reset(token)
        entries.extend(CallTraceEntry(kind, label, seconds) for kind, label, seconds in raw)


TRACE_CALL_LIMIT = 40
"""At most this many calls are listed one by one in :func:`call_trace_json` (the slowest); the totals count them all."""


def call_trace_json(entries: Sequence[CallTraceEntry], *, limit: int = TRACE_CALL_LIMIT) -> dict[str, Any]:
    """Per kind (``git``, ``gerrit``) the count and milliseconds, and the slowest calls in the order made."""
    totals = {kind: {"count": 0, "ms": 0.0} for kind in ("git", "gerrit")}
    for entry in entries:
        total = totals.setdefault(entry.kind, {"count": 0, "ms": 0.0})
        total["count"] += 1
        total["ms"] += entry.seconds * 1000
    kept = set(sorted(range(len(entries)), key=lambda index: entries[index].seconds, reverse=True)[:limit])
    calls = [
        [entry.kind, entry.label[:200], round(entry.seconds * 1000, 1)]
        for index, entry in enumerate(entries)
        if index in kept
    ]
    for total in totals.values():
        total["ms"] = round(total["ms"], 1)
    return {**totals, "calls": calls, "calls_left_out": len(entries) - len(calls)}


def gerrit_request_label(
    method: str,
    path: str,
    *,
    params: dict[str, str] | list[tuple[str, str]] | None,
) -> str:
    """Compact label for a Gerrit REST request."""

    base = f"{method} {path}"
    if not params:
        return base
    items = list(params.items()) if isinstance(params, dict) else list(params)
    q_vals = [value for key, value in items if key == "q"]
    if not q_vals:
        return base
    query = q_vals[0]
    ref_count = query.count(" OR ") + 1
    n_vals = [value for key, value in items if key == "n"]
    opt_vals = [value for key, value in items if key == "o"]
    suffix = f" ({ref_count} refs)"
    if n_vals:
        suffix += f" n={n_vals[0]}"
    if opt_vals:
        suffix += f" o={','.join(opt_vals)}"
    return f"{base}?q=…{suffix}"


def gerrit_request_label_from_url(method: str, url: str) -> str:
    """Compact label derived from a fully built Gerrit URL."""

    parsed = urlparse(url)
    path = parsed.path
    prefix = "/a/"
    if path.startswith(prefix):
        path = path[len(prefix) :]
    query = parse_qs(parsed.query, keep_blank_values=True)
    params: list[tuple[str, str]] = []
    for key, values in query.items():
        for value in values:
            params.append((key, value))
    return gerrit_request_label(method, path, params=params)


def run_traced(trace_file: str, argv: Sequence[str], run: Callable[[], int]) -> int:
    """Run one command, then append how it went to *trace_file* as one JSON line (``kind: "cli"``).

    For ``GER_TRACE``: a ger command a terminal runs is measured beside the VS Code extension's
    records in the same file (``vscode/README.md#tracing``). Only a command that returns is written;
    a file that cannot be written is skipped.
    """
    started = time.time()
    began = time.perf_counter()
    with collect_calls() as calls:
        code = run()
    took = time.perf_counter() - began
    record = {
        "kind": "cli",
        "t": round(started * 1000, 1),
        "argv": list(argv),
        "exit": code,
        "ms": round(took * 1000, 1),
    }
    record.update(call_trace_json(calls))
    try:
        with open(trace_file, "a", encoding="utf-8") as out:
            out.write(json.dumps(record) + "\n")
    except OSError:
        pass
    return code


def _aggregate_label(kind: str, label: str) -> str:
    if kind != "gerrit":
        return label
    return _CHANGE_PATH_RE.sub(r"\1 changes/{change}/", label)


def format_call_trace_summary(collected: Sequence[CallTraceEntry] | None = None) -> str:
    """A human-readable timing report of *collected*, else of what ``--debug-log`` recorded; ``""`` when empty."""

    entries = list(collected) if collected is not None else [CallTraceEntry(*record) for record in _records]
    if not entries:
        return ""
    total = sum(entry.seconds for entry in entries)
    lines = [f"request trace ({total * 1000:.0f}ms total):"]
    for kind in ("gerrit", "git"):
        group = [entry for entry in entries if entry.kind == kind]
        if not group:
            continue
        group_total = sum(entry.seconds for entry in group)
        lines.append(f"  {kind} {len(group)} call(s), {group_total * 1000:.0f}ms")
        aggregated: dict[str, tuple[int, float]] = {}
        for entry in group:
            key = _aggregate_label(kind, entry.label)
            count, seconds = aggregated.get(key, (0, 0.0))
            aggregated[key] = (count + 1, seconds + entry.seconds)
        for label, (count, seconds) in sorted(aggregated.items(), key=lambda item: item[1][1], reverse=True):
            count_suffix = f" x{count}" if count > 1 else ""
            lines.append(f"    {seconds * 1000:6.0f}ms{count_suffix}  {label}")
    return "\n".join(lines)
