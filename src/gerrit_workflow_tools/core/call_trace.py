"""High-level git/Gerrit call timing for ``--debug-log``."""

from __future__ import annotations

import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse


_ENABLED = False
_records: list[tuple[str, str, float]] = []
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
    """Record one traced call when tracing is enabled."""

    if not _ENABLED:
        return
    _records.append((kind, label, seconds))


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


def _aggregate_label(kind: str, label: str) -> str:
    if kind != "gerrit":
        return label
    return _CHANGE_PATH_RE.sub(r"\1 changes/{change}/", label)


def format_call_trace_summary() -> str:
    """Return a human-readable timing report, or ``""`` when nothing was traced."""

    if not _records:
        return ""
    entries = [CallTraceEntry(kind, label, seconds) for kind, label, seconds in _records]
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


def call_totals() -> dict[str, tuple[int, float]]:
    """Calls traced so far, by kind (``git``, ``gerrit``): how many and how long in all."""

    totals: dict[str, tuple[int, float]] = {}
    for kind, _label, seconds in _records:
        count, spent = totals.get(kind, (0, 0.0))
        totals[kind] = (count + 1, spent + seconds)
    return totals


@dataclass
class _Window:
    requests: int = 0
    calls: dict[str, tuple[int, float]] = field(default_factory=dict)

    def add(self, totals: dict[str, tuple[int, float]]) -> None:
        self.requests += 1
        for kind, (count, seconds) in totals.items():
            had, spent = self.calls.get(kind, (0, 0.0))
            self.calls[kind] = (had + count, spent + seconds)

    def describe(self, *, timed: bool) -> str:
        parts = [f"{self.requests} request(s)"]
        for kind in ("git", "gerrit"):
            count, seconds = self.calls.get(kind, (0, 0.0))
            parts.append(f"{kind} {count} call(s), {seconds * 1000:.0f}ms" if timed else f"{kind} {count}")
        return ", ".join(parts)


class CallTally:
    """Calls of a long-running process, summed per request; a summary line about once a *period*.

    Each request's :func:`call_totals` is added with :meth:`add`, which returns the summary
    of the period just past once it is over (``None`` until then): there is no timer, so a
    quiet period says nothing.
    """

    def __init__(self, *, period: float = 60.0, clock: Callable[[], float] = time.monotonic) -> None:
        self._period = period
        self._clock = clock
        self._started = self._window_start = clock()
        self._window = _Window()
        self._all = _Window()

    def add(self, totals: dict[str, tuple[int, float]]) -> str | None:
        """Count one request's calls; the summary line when this request closes a period."""

        self._window.add(totals)
        self._all.add(totals)
        now = self._clock()
        if now - self._window_start < self._period:
            return None
        line = (
            f"calls in the last {now - self._window_start:.0f}s: {self._window.describe(timed=True)}"
            f" (since start {(now - self._started) / 60:.0f} min: {self._all.describe(timed=False)})"
        )
        self._window_start = now
        self._window = _Window()
        return line


def print_call_trace_summary() -> None:
    """Print the timing report to stderr when tracing collected any calls."""

    summary = format_call_trace_summary()
    if summary:
        sys.stdout.flush()
        print(f"\n{summary}", file=sys.stderr)
