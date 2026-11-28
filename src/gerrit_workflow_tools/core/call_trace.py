"""High-level git/Gerrit/CI call timing for ``--debug-log``, with the size of each HTTP response."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse


_ENABLED = False
_records: list[tuple[str, str, float, int | None]] = []
_CHANGE_PATH_RE = re.compile(r"^(GET|POST|PUT|DELETE) changes/[^/]+/")


@dataclass(frozen=True)
class CallTraceEntry:
    """One traced subprocess or HTTP round trip."""

    kind: str
    label: str
    seconds: float
    size: int | None = None
    """Bytes of the response body, for an HTTP call that got one."""


def set_call_trace_enabled(enabled: bool) -> None:
    """Turn request tracing on or off for the current CLI run."""

    global _ENABLED  # pylint: disable=global-statement
    _ENABLED = enabled
    if not enabled:
        reset_call_trace()


def reset_call_trace() -> None:
    """Drop collected records (mainly for tests)."""

    _records.clear()


def record_call(kind: str, label: str, seconds: float, size: int | None = None) -> None:
    """Record one traced call when tracing is enabled; *size* is the response body's bytes."""

    if not _ENABLED:
        return
    _records.append((kind, label, seconds, size))


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
    entries = [CallTraceEntry(*record) for record in _records]
    total = sum(entry.seconds for entry in entries)
    lines = [f"request trace ({total * 1000:.0f}ms total):"]
    for kind in ("gerrit", "ci", "git"):
        group = [entry for entry in entries if entry.kind == kind]
        if not group:
            continue
        group_total = sum(entry.seconds for entry in group)
        lines.append(f"  {kind} {len(group)} call(s), {group_total * 1000:.0f}ms{_size_suffix(group)}")
        aggregated: dict[str, list[CallTraceEntry]] = {}
        for entry in group:
            aggregated.setdefault(_aggregate_label(kind, entry.label), []).append(entry)
        rows = sorted(aggregated.items(), key=lambda item: sum(e.seconds for e in item[1]), reverse=True)
        for label, same in rows:
            count_suffix = f" x{len(same)}" if len(same) > 1 else ""
            seconds = sum(e.seconds for e in same)
            lines.append(f"    {seconds * 1000:6.0f}ms{count_suffix}  {label}{_size_suffix(same)}")
    return "\n".join(lines)


def format_size(size: int) -> str:
    """Bytes as a short size: ``512 B``, ``14.2 kB``, ``3.1 MB``."""

    if size < 1000:
        return f"{size} B"
    if size < 999_950:  # what would round to 1000.0 kB reads as 1.0 MB
        return f"{size / 1000:.1f} kB"
    return f"{size / 1_000_000:.1f} MB"


def _size_suffix(entries: list[CallTraceEntry]) -> str:
    """``, 14.2 kB`` summed over the entries that got a response body; empty when none did."""

    sizes = [entry.size for entry in entries if entry.size is not None]
    return f", {format_size(sum(sizes))}" if sizes else ""


def print_call_trace_summary() -> None:
    """Print the timing report to stderr when tracing collected any calls."""

    summary = format_call_trace_summary()
    if summary:
        sys.stdout.flush()
        print(f"\n{summary}", file=sys.stderr)
