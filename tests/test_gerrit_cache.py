"""Tests for the layered Gerrit API cache."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gerrit_workflow_tools.core.gerrit.cache import GerritCache


def _change(change_id: str, updated: str) -> dict[str, Any]:
    return {
        "change_id": change_id,
        "_number": 123,
        "updated": updated,
        "subject": "cached",
    }


def test_change_cache_skips_probe_inside_trust_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cid = "Iaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    cache = GerritCache(tmp_path / "cache.db", web_base="https://g.example")
    monkeypatch.setattr("gerrit_workflow_tools.core.gerrit.cache._now", lambda: 1000)
    cache.upsert_changes({cid: _change(cid, "u1")})

    def probe_updated(_ids: list[str]) -> dict[str, str]:
        raise AssertionError("freshness probe should be skipped inside trust window")

    def fetch_changes(_ids: list[str]) -> dict[str, dict[str, Any]]:
        raise AssertionError("fresh cache hit should not refetch")

    monkeypatch.setattr("gerrit_workflow_tools.core.gerrit.cache._now", lambda: 1009)
    rows = cache.load_changes(
        [cid],
        probe_updated=probe_updated,
        fetch_changes=fetch_changes,
        trust_window_seconds=10,
    )
    assert rows[cid]["updated"] == "u1"


def test_change_cache_probes_after_trust_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cid = "Ibbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    cache = GerritCache(tmp_path / "cache.db", web_base="https://g.example")
    monkeypatch.setattr("gerrit_workflow_tools.core.gerrit.cache._now", lambda: 1000)
    cache.upsert_changes({cid: _change(cid, "u1")})
    probed: list[list[str]] = []

    def probe_updated(ids: list[str]) -> dict[str, str]:
        probed.append(ids)
        return {cid: "u1"}

    def fetch_changes(_ids: list[str]) -> dict[str, dict[str, Any]]:
        raise AssertionError("matching updated timestamp should keep cached payload")

    monkeypatch.setattr("gerrit_workflow_tools.core.gerrit.cache._now", lambda: 1011)
    rows = cache.load_changes(
        [cid],
        probe_updated=probe_updated,
        fetch_changes=fetch_changes,
        trust_window_seconds=10,
    )
    assert probed == [[cid]]
    assert rows[cid]["updated"] == "u1"


def test_change_cache_refetches_when_updated_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cid = "Icccccccccccccccccccccccccccccccccccccccc"
    cache = GerritCache(tmp_path / "cache.db", web_base="https://g.example")
    monkeypatch.setattr("gerrit_workflow_tools.core.gerrit.cache._now", lambda: 1000)
    cache.upsert_changes({cid: _change(cid, "u1")})

    def probe_updated(_ids: list[str]) -> dict[str, str]:
        return {cid: "u2"}

    def fetch_changes(ids: list[str]) -> dict[str, dict[str, Any]]:
        assert ids == [cid]
        return {cid: _change(cid, "u2")}

    monkeypatch.setattr("gerrit_workflow_tools.core.gerrit.cache._now", lambda: 1011)
    rows = cache.load_changes(
        [cid],
        probe_updated=probe_updated,
        fetch_changes=fetch_changes,
        trust_window_seconds=10,
    )
    assert rows[cid]["updated"] == "u2"
