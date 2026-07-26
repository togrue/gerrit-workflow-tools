"""Tests for the layered Gerrit API cache."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gerrit_workflow_tools.core.gerrit.cache import GerritCache


def _change(
    triplet: str,
    *,
    change_id: str | None = None,
    updated: str = "u1",
    branch: str | None = None,
    number: int = 123,
) -> dict[str, Any]:
    parts = triplet.split("~")
    footer = change_id if change_id is not None else parts[-1]
    br = branch if branch is not None else (parts[1] if len(parts) == 3 else "main")
    return {
        "id": triplet,
        "change_id": footer,
        "branch": br,
        "_number": number,
        "updated": updated,
        "subject": "cached",
        "status": "NEW",
    }


def test_change_cache_skips_probe_inside_trust_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    triplet = "proj~main~Iaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    cache = GerritCache(tmp_path / "cache.db", web_base="https://g.example")
    monkeypatch.setattr("gerrit_workflow_tools.core.gerrit.cache._now", lambda: 1000)
    cache.upsert_changes([_change(triplet, updated="u1")])

    def probe_updated(_ids: list[str]) -> dict[str, str]:
        raise AssertionError("freshness probe should be skipped inside trust window")

    def fetch_changes(_ids: list[str]) -> dict[str, dict[str, Any]]:
        raise AssertionError("fresh cache hit should not refetch")

    monkeypatch.setattr("gerrit_workflow_tools.core.gerrit.cache._now", lambda: 1009)
    rows = cache.load_changes(
        [triplet],
        probe_updated=probe_updated,
        fetch_changes=fetch_changes,
        trust_window_seconds=10,
    )
    assert rows[triplet]["updated"] == "u1"


def test_change_cache_probes_after_trust_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    triplet = "proj~main~Ibbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    cache = GerritCache(tmp_path / "cache.db", web_base="https://g.example")
    monkeypatch.setattr("gerrit_workflow_tools.core.gerrit.cache._now", lambda: 1000)
    cache.upsert_changes([_change(triplet, updated="u1")])
    probed: list[list[str]] = []

    def probe_updated(ids: list[str]) -> dict[str, str]:
        probed.append(ids)
        return {triplet: "u1"}

    def fetch_changes(_ids: list[str]) -> dict[str, dict[str, Any]]:
        raise AssertionError("matching updated timestamp should keep cached payload")

    monkeypatch.setattr("gerrit_workflow_tools.core.gerrit.cache._now", lambda: 1011)
    rows = cache.load_changes(
        [triplet],
        probe_updated=probe_updated,
        fetch_changes=fetch_changes,
        trust_window_seconds=10,
    )
    assert probed == [[triplet]]
    assert rows[triplet]["updated"] == "u1"


def test_change_cache_refetches_when_updated_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    triplet = "proj~main~Icccccccccccccccccccccccccccccccccccccccc"
    cache = GerritCache(tmp_path / "cache.db", web_base="https://g.example")
    monkeypatch.setattr("gerrit_workflow_tools.core.gerrit.cache._now", lambda: 1000)
    cache.upsert_changes([_change(triplet, updated="u1")])

    def probe_updated(_ids: list[str]) -> dict[str, str]:
        return {triplet: "u2"}

    def fetch_changes(ids: list[str]) -> dict[str, dict[str, Any]]:
        assert ids == [triplet]
        return {triplet: _change(triplet, updated="u2")}

    monkeypatch.setattr("gerrit_workflow_tools.core.gerrit.cache._now", lambda: 1011)
    rows = cache.load_changes(
        [triplet],
        probe_updated=probe_updated,
        fetch_changes=fetch_changes,
        trust_window_seconds=10,
    )
    assert rows[triplet]["updated"] == "u2"


def test_change_cache_stores_same_footer_change_id_on_different_branches_independently(
    tmp_path: Path,
) -> None:
    footer = "Idddddddddddddddddddddddddddddddddddddddd"
    main_triplet = f"proj~main~{footer}"
    dev_triplet = f"proj~dev~{footer}"
    cache = GerritCache(tmp_path / "cache.db", web_base="https://g.example")

    cache.upsert_changes(
        [
            _change(main_triplet, change_id=footer, updated="main-u"),
            _change(dev_triplet, change_id=footer, updated="dev-u"),
        ]
    )

    rows = cache._lookup_changes([main_triplet, dev_triplet])
    assert set(rows) == {main_triplet, dev_triplet}
    assert rows[main_triplet].payload["updated"] == "main-u"
    assert rows[dev_triplet].payload["updated"] == "dev-u"


def test_change_cache_treats_differently_cased_triplets_as_distinct(tmp_path: Path) -> None:
    upper = "proj~main~Ieeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
    lower = "proj~main~ieeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
    cache = GerritCache(tmp_path / "cache.db", web_base="https://g.example")

    cache.upsert_changes(
        [
            _change(upper, updated="upper-u"),
            _change(lower, updated="lower-u"),
        ]
    )

    rows = cache._lookup_changes([upper, lower])
    assert set(rows) == {upper, lower}
    assert rows[upper].payload["updated"] == "upper-u"
    assert rows[lower].payload["updated"] == "lower-u"


def test_schema_version_bump_clears_old_cache(tmp_path: Path) -> None:
    import sqlite3

    triplet = "proj~main~Iffffffffffffffffffffffffffffffffffffffff"
    db_path = tmp_path / "cache.db"
    cache = GerritCache(db_path, web_base="https://g.example")
    cache.upsert_changes([_change(triplet)])
    assert cache.info().changes == 1

    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE meta SET value = '1' WHERE key = 'schema_version'")

    cleared = GerritCache(db_path, web_base="https://g.example")
    assert cleared.info().changes == 0


def test_find_payloads_by_footer_change_ids_dedupes_aliases(tmp_path: Path) -> None:
    cid = "Iaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    main = _change(f"proj~main~{cid}", branch="main", number=1)
    dev = _change(f"proj~dev~{cid}", branch="dev", number=2)
    compact = dict(main)
    compact["id"] = "proj~1"
    cache = GerritCache(tmp_path / "cache.db", web_base="https://g.example")
    # Same main payload under triplet + compact keys; plus other branch.
    cache.upsert_changes({f"proj~main~{cid}": main, "proj~1": compact, f"proj~dev~{cid}": dev})

    found = cache.find_payloads_by_footer_change_ids([cid])
    assert len(found[cid]) == 2
    numbers = {row["_number"] for row in found[cid]}
    assert numbers == {1, 2}
