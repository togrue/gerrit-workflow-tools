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
        conn.execute("UPDATE meta SET value = '2' WHERE key = 'schema_version'")

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


def _comment_map(body: str) -> dict[str, list[dict[str, Any]]]:
    return {"file.py": [{"id": "c1", "message": body, "unresolved": True}]}


def test_load_comments_serves_cache_while_change_updated_is_unchanged(tmp_path: Path) -> None:
    """Comments outlive the trust window as long as the change itself has not moved."""

    cache = GerritCache(tmp_path / "c.db", web_base="https://gerrit.example.com")
    calls: list[str] = []

    def fetch(triplet: str) -> dict[str, list[dict[str, Any]]]:
        calls.append(triplet)
        return _comment_map("first")

    first = cache.load_comments(
        "proj~main~I1",
        fetch_comments=fetch,
        change_updated="u1",
        trust_window_seconds=0,
    )
    second = cache.load_comments(
        "proj~main~I1",
        fetch_comments=fetch,
        change_updated="u1",
        trust_window_seconds=0,
    )

    assert calls == ["proj~main~I1"], "second read must not hit the network"
    assert first == second == _comment_map("first")


def test_load_comments_refetches_when_change_updated_moves(tmp_path: Path) -> None:
    """A bumped ``updated`` invalidates the cached comments even inside the trust window."""

    cache = GerritCache(tmp_path / "c.db", web_base="https://gerrit.example.com")
    bodies = iter(["first", "second"])

    def fetch(triplet: str) -> dict[str, list[dict[str, Any]]]:
        return _comment_map(next(bodies))

    cache.load_comments("proj~main~I1", fetch_comments=fetch, change_updated="u1", trust_window_seconds=0)
    after = cache.load_comments("proj~main~I1", fetch_comments=fetch, change_updated="u2", trust_window_seconds=0)

    assert after == _comment_map("second")


def test_capability_round_trips_and_starts_unknown(tmp_path: Path) -> None:
    cache = GerritCache(tmp_path / "c.db", web_base="https://gerrit.example.com")

    assert cache.capability("checks") is None
    cache.set_capability("checks", False)
    assert cache.capability("checks") is False
    cache.set_capability("checks", True)
    assert cache.capability("checks") is True


def test_capability_expires_so_a_newly_installed_plugin_is_rediscovered(tmp_path: Path) -> None:
    cache = GerritCache(tmp_path / "c.db", web_base="https://gerrit.example.com")
    cache.set_capability("checks", False)

    assert cache.capability("checks", ttl_seconds=0) is None


def test_cache_clear_forgets_capabilities_but_keeps_schema_metadata(tmp_path: Path) -> None:
    cache = GerritCache(tmp_path / "c.db", web_base="https://gerrit.example.com")
    cache.set_capability("checks", False)

    cache.clear()

    assert cache.capability("checks") is None
    assert cache.info().host == cache.host


def test_upsert_changes_strips_avatars(tmp_path: Path) -> None:
    triplet = "proj~main~I1111111111111111111111111111111111111111"
    cache = GerritCache(tmp_path / "c.db", web_base="https://g.example")
    payload = _change(triplet)
    payload["owner"] = {"username": "alice", "avatars": [{"url": "http://x/a.png", "height": 1}]}
    cache.upsert_changes([payload])
    rows = cache._lookup_changes([triplet])
    owner = rows[triplet].payload.get("owner")
    assert isinstance(owner, dict)
    assert "avatars" not in owner
    assert owner.get("username") == "alice"


def test_missing_change_negative_cache_skips_refetch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    triplet = "proj~main~Innnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnn"
    cache = GerritCache(tmp_path / "c.db", web_base="https://g.example")
    monkeypatch.setattr("gerrit_workflow_tools.core.gerrit.cache._now", lambda: 1000)

    def fetch(_ids: list[str]) -> dict[str, dict[str, Any]]:
        return {}

    cache.load_changes([triplet], probe_updated=lambda _ids: {}, fetch_changes=fetch, missing_ttl_seconds=60)
    calls = {"n": 0}

    def fetch_again(_ids: list[str]) -> dict[str, dict[str, Any]]:
        calls["n"] += 1
        return {}

    monkeypatch.setattr("gerrit_workflow_tools.core.gerrit.cache._now", lambda: 1030)
    cache.load_changes([triplet], probe_updated=lambda _ids: {}, fetch_changes=fetch_again, missing_ttl_seconds=60)
    assert calls["n"] == 0


def test_load_checks_serves_cache_while_change_updated_is_unchanged(tmp_path: Path) -> None:
    cache = GerritCache(tmp_path / "c.db", web_base="https://gerrit.example.com")
    calls: list[str] = []

    def fetch(triplet: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        calls.append(triplet)
        return ([{"checker_name": "x", "status": "FAILED"}], [{"message": "fail"}])

    first = cache.load_checks("proj~main~I1", fetch_checks=fetch, change_updated="u1", trust_window_seconds=0)
    second = cache.load_checks("proj~main~I1", fetch_checks=fetch, change_updated="u1", trust_window_seconds=0)

    assert calls == ["proj~main~I1"]
    assert first == second


def test_delta_query_refreshes_certified_stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    triplet = "proj~main~Idddddddddddddddddddddddddddddddddddddddd"
    cache = GerritCache(tmp_path / "c.db", web_base="https://g.example")
    scope_key = "host~proj"
    monkeypatch.setattr("gerrit_workflow_tools.core.gerrit.cache._now", lambda: 1000)

    def probe(_ids: list[str]) -> dict[str, str]:
        return {triplet: "2024-01-01 10:00:00.000000000"}

    def fetch(ids: list[str]) -> dict[str, dict[str, Any]]:
        return {triplet: _change(triplet, updated="2024-01-01 10:00:00.000000000")}

    cache.load_changes(
        [triplet],
        probe_updated=probe,
        fetch_changes=fetch,
        scope_key=scope_key,
        trust_window_seconds=0,
    )

    monkeypatch.setattr("gerrit_workflow_tools.core.gerrit.cache._now", lambda: 2000)
    delta_calls: list[str] = []

    def fetch_delta(since: str) -> tuple[list[dict[str, Any]], bool]:
        delta_calls.append(since)
        return [], True

    rows = cache.load_changes(
        [triplet],
        probe_updated=lambda _ids: (_ for _ in ()).throw(AssertionError("probe")),
        fetch_changes=lambda _ids: (_ for _ in ()).throw(AssertionError("fetch")),
        fetch_delta=fetch_delta,
        scope_key=scope_key,
        trust_window_seconds=0,
    )
    assert delta_calls == ["2024-01-01 10:00:00.000000000"]
    assert rows[triplet]["updated"] == "2024-01-01 10:00:00.000000000"


def test_delta_not_used_when_certification_generation_mismatches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row cached under an older scope generation must not be served via delta alone."""

    triplet_a = "proj~main~Iaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    triplet_b = "proj~main~Ibbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    cache = GerritCache(tmp_path / "c.db", web_base="https://g.example")
    scope_key = "host~proj"
    monkeypatch.setattr("gerrit_workflow_tools.core.gerrit.cache._now", lambda: 1000)

    cache.upsert_changes([_change(triplet_a, updated="2024-01-01 09:00:00.000000000")])
    cache.load_changes(
        [triplet_b],
        probe_updated=lambda _ids: {triplet_b: "2024-01-01 10:00:00.000000000"},
        fetch_changes=lambda ids: {triplet_b: _change(triplet_b, updated="2024-01-01 10:00:00.000000000")},
        scope_key=scope_key,
        trust_window_seconds=0,
    )

    probed: list[list[str]] = []

    def probe(ids: list[str]) -> dict[str, str]:
        probed.append(ids)
        return {
            triplet_a: "2024-01-01 09:00:00.000000000",
            triplet_b: "2024-01-01 10:00:00.000000000",
        }

    monkeypatch.setattr("gerrit_workflow_tools.core.gerrit.cache._now", lambda: 2000)
    cache.load_changes(
        [triplet_a, triplet_b],
        probe_updated=probe,
        fetch_changes=lambda _ids: {},
        fetch_delta=lambda _since: (_ for _ in ()).throw(AssertionError("delta")),
        scope_key=scope_key,
        trust_window_seconds=0,
    )
    assert probed, "uncertified row must fall back to probe path"
