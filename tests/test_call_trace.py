"""Tests for high-level request tracing with ``--debug-log``."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from gerrit_workflow_tools import cli_ger
from gerrit_workflow_tools.core.call_trace import (
    CallTraceEntry,
    call_trace_json,
    collect_calls,
    format_call_trace_summary,
    gerrit_request_label,
    gerrit_request_label_from_url,
    record_call,
    reset_call_trace,
    set_call_trace_enabled,
)
from gerrit_workflow_tools.core.gerrit.rest import parallel_map


def test_gerrit_request_label_summarizes_batch_query() -> None:
    label = gerrit_request_label(
        "GET",
        "changes/",
        params=[
            ("q", "project:demo (change:Iaaa OR change:Ibbb OR change:Iccc)"),
            ("n", "85"),
            ("o", "SKIP_DIFFSTAT"),
        ],
    )
    assert label == "GET changes/?q=… (3 refs) n=85 o=SKIP_DIFFSTAT"


def test_gerrit_request_label_from_url() -> None:
    label = gerrit_request_label_from_url(
        "GET",
        "http://gerrit/a/changes/?q=change%3AIabc+OR+change%3AIdef&n=25&o=DETAILED_LABELS",
    )
    assert label == "GET changes/?q=… (2 refs) n=25 o=DETAILED_LABELS"


def test_format_call_trace_summary_groups_and_sorts() -> None:
    set_call_trace_enabled(True)
    try:
        record_call("git", "git log --reverse", 0.010)
        record_call("git", "git config --list", 0.002)
        record_call("gerrit", "GET changes/?q=… (25 refs)", 0.500)
        record_call("gerrit", "GET changes/test~1/comments", 0.050)
        summary = format_call_trace_summary()
    finally:
        set_call_trace_enabled(False)
        reset_call_trace()

    assert "request trace (562ms total):" in summary
    assert "gerrit 2 call(s), 550ms" in summary
    assert "500ms  GET changes/?q=… (25 refs)" in summary
    assert "git 2 call(s), 12ms" in summary
    assert "git log --reverse" in summary
    assert "git config --list" in summary


def test_format_call_trace_summary_aggregates_repeated_labels() -> None:
    set_call_trace_enabled(True)
    try:
        record_call("gerrit", "GET changes/test~1/reviewers/", 0.020)
        record_call("gerrit", "GET changes/test~1/reviewers/", 0.015)
        summary = format_call_trace_summary()
    finally:
        set_call_trace_enabled(False)
        reset_call_trace()

    assert "35ms x2  GET changes/{change}/reviewers/" in summary


def test_format_call_trace_summary_normalizes_change_paths() -> None:
    set_call_trace_enabled(True)
    try:
        record_call("gerrit", "GET changes/test~1/reviewers/", 0.020)
        record_call("gerrit", "GET changes/test~2/reviewers/", 0.015)
        summary = format_call_trace_summary()
    finally:
        set_call_trace_enabled(False)
        reset_call_trace()

    assert "35ms x2  GET changes/{change}/reviewers/" in summary


def test_collect_calls_sees_its_own_context_and_its_parallel_workers_only() -> None:
    """A ``ger serve`` request's calls: its own and its ``parallel_map`` workers', not a background thread's."""

    background = threading.Thread(target=lambda: record_call("git", "git fetch", 0.5))
    with collect_calls() as calls:
        record_call("git", "git status", 0.01)
        parallel_map(
            [
                lambda: record_call("gerrit", "GET changes/1/", 0.02),
                lambda: record_call("gerrit", "GET changes/2/", 0.03),
            ]
        )
        background.start()
        background.join()
        assert calls == [], "filled when the block ends"
    assert sorted(entry.label for entry in calls) == ["GET changes/1/", "GET changes/2/", "git status"]
    record_call("git", "git log", 0.01)
    assert len(calls) == 3, "nothing is recorded outside the block"
    assert format_call_trace_summary() == "", "collected calls do not reach the --debug-log list"


def test_call_trace_json_counts_all_and_lists_the_slowest_in_order() -> None:

    entries = [CallTraceEntry("git", f"git {n}", n / 1000) for n in (5, 1, 9, 3)]
    entries.append(CallTraceEntry("gerrit", "GET changes/", 0.1))
    shaped = call_trace_json(entries, limit=3)
    assert shaped["git"] == {"count": 4, "ms": 18.0}
    assert shaped["gerrit"] == {"count": 1, "ms": 100.0}
    assert shaped["calls"] == [["git", "git 5", 5.0], ["git", "git 9", 9.0], ["gerrit", "GET changes/", 100.0]]
    assert shaped["calls_left_out"] == 2


def test_ger_trace_appends_one_record_per_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``GER_TRACE``: a ger command run in a terminal is measured beside the extension's records."""
    trace = tmp_path / "trace.jsonl"
    trace.write_text('{"kind": "action"}\n', encoding="utf-8")
    monkeypatch.setenv("GER_TRACE", str(trace))

    def handler(_argv: list[str]) -> int:
        record_call("git", "git status", 0.004)
        return 3

    monkeypatch.setattr(cli_ger, "_load_handler", lambda _path: handler)
    cli_ger._HANDLER_CACHE.clear()
    try:
        assert cli_ger.main(["log", "-v"]) == 3
    finally:
        cli_ger._HANDLER_CACHE.clear()
    first, record = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    assert first == {"kind": "action"}, "appended"
    assert (record["kind"], record["argv"], record["exit"]) == ("cli", ["log", "-v"], 3)
    assert record["git"] == {"count": 1, "ms": 4.0}
    assert record["t"] > 0 and record["ms"] >= 0
