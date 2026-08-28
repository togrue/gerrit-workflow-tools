"""Tests for high-level request tracing with ``--debug-log``."""

from __future__ import annotations

from gerrit_workflow_tools.core.call_trace import (
    format_call_trace_summary,
    gerrit_request_label,
    gerrit_request_label_from_url,
    record_call,
    reset_call_trace,
    set_call_trace_enabled,
)


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
