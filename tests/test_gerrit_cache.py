"""Tests for the layered Gerrit API cache."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gerrit_workflow_tools.core.gerrit.cache import GerritCache
from gerrit_workflow_tools.core.gerrit.change_queries import change_freshness_key


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


def test_a_probe_that_confirms_a_row_starts_its_trust_window_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gerrit said the row is current: asking again within the window would only repeat the request."""
    triplet = "proj~main~Ibbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    cache = GerritCache(tmp_path / "cache.db", web_base="https://g.example")
    clock = {"t": 1000}
    monkeypatch.setattr("gerrit_workflow_tools.core.gerrit.cache._now", lambda: clock["t"])
    cache.upsert_changes([_change(triplet, updated="u1")])
    probed: list[list[str]] = []

    def probe_updated(ids: list[str]) -> dict[str, str]:
        probed.append(ids)
        return {triplet: "u1"}

    def fetch_changes(_ids: list[str]) -> dict[str, dict[str, Any]]:
        raise AssertionError("an unchanged row is never fetched again")

    def load() -> None:
        cache.load_changes([triplet], probe_updated=probe_updated, fetch_changes=fetch_changes, trust_window_seconds=10)

    clock["t"] = 1011
    load()
    assert len(probed) == 1
    clock["t"] = 1020  # 9 s after the probe, 20 s after the fetch
    load()
    assert len(probed) == 1
    clock["t"] = 1021
    load()
    assert len(probed) == 2


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
    """A bumped freshness token invalidates cached comments even inside the trust window."""

    cache = GerritCache(tmp_path / "c.db", web_base="https://gerrit.example.com")
    bodies = iter(["first", "second"])

    def fetch(triplet: str) -> dict[str, list[dict[str, Any]]]:
        return _comment_map(next(bodies))

    cache.load_comments("proj~main~I1", fetch_comments=fetch, change_updated="u1", trust_window_seconds=0)
    after = cache.load_comments("proj~main~I1", fetch_comments=fetch, change_updated="u2", trust_window_seconds=0)

    assert after == _comment_map("second")


def test_load_comments_refetches_on_token_change_inside_trust_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = GerritCache(tmp_path / "c.db", web_base="https://gerrit.example.com")
    monkeypatch.setattr("gerrit_workflow_tools.core.gerrit.cache._now", lambda: 1000)
    bodies = iter(["first", "second"])

    def fetch(_triplet: str) -> dict[str, list[dict[str, Any]]]:
        return _comment_map(next(bodies))

    cache.load_comments("proj~main~I1", fetch_comments=fetch, change_updated="meta-a", trust_window_seconds=10)
    after = cache.load_comments("proj~main~I1", fetch_comments=fetch, change_updated="meta-b", trust_window_seconds=10)
    assert after == _comment_map("second")


def test_load_comments_context_key_does_not_share_rows(tmp_path: Path) -> None:
    cache = GerritCache(tmp_path / "c.db", web_base="https://gerrit.example.com")
    calls: list[str] = []

    def fetch_plain(triplet: str) -> dict[str, list[dict[str, Any]]]:
        calls.append(f"plain:{triplet}")
        return _comment_map("plain")

    def fetch_ctx(triplet: str) -> dict[str, list[dict[str, Any]]]:
        calls.append(f"ctx:{triplet}")
        return {
            "file.py": [
                {
                    "id": "c1",
                    "message": "ctx",
                    "unresolved": True,
                    "context_lines": [{"line_number": 1, "context_line": "src"}],
                }
            ]
        }

    cache.load_comments(
        "proj~main~I1",
        fetch_comments=fetch_plain,
        change_updated="u1",
        trust_window_seconds=0,
    )
    cache.load_comments(
        "proj~main~I1",
        fetch_comments=fetch_ctx,
        change_updated="u1",
        trust_window_seconds=0,
        context_key="p2",
    )
    plain = cache.load_comments(
        "proj~main~I1",
        fetch_comments=fetch_plain,
        change_updated="u1",
        trust_window_seconds=0,
    )
    ctx = cache.load_comments(
        "proj~main~I1",
        fetch_comments=fetch_ctx,
        change_updated="u1",
        trust_window_seconds=0,
        context_key="p2",
    )
    assert calls == ["plain:proj~main~I1", "ctx:proj~main~I1"]
    assert plain == _comment_map("plain")
    assert ctx["file.py"][0]["context_lines"][0]["context_line"] == "src"


def test_change_cache_refetches_when_meta_rev_id_moves_but_updated_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    triplet = "proj~main~Ieeeefeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
    cache = GerritCache(tmp_path / "cache.db", web_base="https://g.example")
    monkeypatch.setattr("gerrit_workflow_tools.core.gerrit.cache._now", lambda: 1000)
    v1 = _change(triplet, updated="t")
    v1["meta_rev_id"] = "meta-a"
    v2 = _change(triplet, updated="t")
    v2["meta_rev_id"] = "meta-b"
    fetched: list[list[str]] = []

    def probe_updated(_ids: list[str]) -> dict[str, str]:
        key = change_freshness_key(v2)
        assert key is not None
        return {triplet: key}

    def fetch_changes(ids: list[str]) -> dict[str, dict[str, Any]]:
        fetched.append(ids)
        return {triplet: v2}

    cache.load_changes(
        [triplet],
        probe_updated=lambda _ids: {triplet: change_freshness_key(v1) or ""},
        fetch_changes=lambda _ids: {triplet: v1},
        trust_window_seconds=0,
    )
    monkeypatch.setattr("gerrit_workflow_tools.core.gerrit.cache._now", lambda: 1011)
    rows = cache.load_changes(
        [triplet],
        probe_updated=probe_updated,
        fetch_changes=fetch_changes,
        trust_window_seconds=10,
    )
    assert fetched == [[triplet]]
    assert rows[triplet]["meta_rev_id"] == "meta-b"


def test_change_cache_refetches_when_comment_counts_move_but_updated_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gerrit hosts without ``meta_rev_id`` still see same-second comment resolves."""

    triplet = "proj~main~Iffffffffffffffffffffffffffffffffffffffff"
    cache = GerritCache(tmp_path / "cache.db", web_base="https://g.example")
    monkeypatch.setattr("gerrit_workflow_tools.core.gerrit.cache._now", lambda: 1000)
    v1 = _change(triplet, updated="t")
    v1["unresolved_comment_count"] = 1
    v1["total_comment_count"] = 1
    v2 = _change(triplet, updated="t")
    v2["unresolved_comment_count"] = 0
    v2["total_comment_count"] = 2

    cache.load_changes(
        [triplet],
        probe_updated=lambda _ids: {triplet: change_freshness_key(v1) or ""},
        fetch_changes=lambda _ids: {triplet: v1},
        trust_window_seconds=0,
    )
    monkeypatch.setattr("gerrit_workflow_tools.core.gerrit.cache._now", lambda: 1011)
    rows = cache.load_changes(
        [triplet],
        probe_updated=lambda _ids: {triplet: change_freshness_key(v2) or ""},
        fetch_changes=lambda _ids: {triplet: v2},
        trust_window_seconds=10,
    )
    assert rows[triplet]["unresolved_comment_count"] == 0


def test_change_freshness_key_prefers_meta_rev_id() -> None:
    assert change_freshness_key({"updated": "t", "meta_rev_id": "abc"}) == "abc"
    assert change_freshness_key({"updated": "t"}) == "t"
    assert (
        change_freshness_key({"updated": "t", "unresolved_comment_count": 1, "total_comment_count": 1})
        == "t|unresolved_comment_count=1|total_comment_count=1"
    )


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


def test_no_such_change_is_trusted_for_the_trust_window_and_no_longer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unpushed stack is not asked about each time, but a push from elsewhere shows as soon as a change would."""
    triplet = "proj~main~Innnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnn"
    cache = GerritCache(tmp_path / "c.db", web_base="https://g.example")
    clock = {"t": 1000}
    monkeypatch.setattr("gerrit_workflow_tools.core.gerrit.cache._now", lambda: clock["t"])
    pushed: dict[str, dict[str, Any]] = {}
    asked: list[int] = []

    def fetch(_ids: list[str]) -> dict[str, dict[str, Any]]:
        asked.append(clock["t"])
        return dict(pushed)

    def load() -> dict[str, dict[str, Any]]:
        return cache.load_changes(
            [triplet], probe_updated=lambda _ids: {}, fetch_changes=fetch, trust_window_seconds=10
        )

    assert load() == {}
    pushed[triplet] = _change(triplet, number=7)  # somebody runs a plain `git push`
    clock["t"] = 1009
    assert load() == {}
    clock["t"] = 1010
    assert triplet in load()
    assert asked == [1000, 1010]


def test_a_clock_set_back_does_not_stretch_the_trust_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A row stamped in the future is not trusted until that time comes: it is checked now."""
    triplet = "proj~main~Icccccccccccccccccccccccccccccccccccccccc"
    cache = GerritCache(tmp_path / "c.db", web_base="https://g.example")
    monkeypatch.setattr("gerrit_workflow_tools.core.gerrit.cache._now", lambda: 5000)
    cache.upsert_changes([_change(triplet, updated="u1")])
    probed: list[list[str]] = []

    def probe(ids: list[str]) -> dict[str, str]:
        probed.append(ids)
        return {triplet: "u1"}

    monkeypatch.setattr("gerrit_workflow_tools.core.gerrit.cache._now", lambda: 1400)  # an hour earlier
    cache.load_changes([triplet], probe_updated=probe, fetch_changes=lambda _ids: {}, trust_window_seconds=10)
    assert probed == [[triplet]]


def test_missing_ttl_does_not_stick_beside_cached_siblings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mixed stack: a prior miss must not stay ``not-pushed`` next to a cached neighbor.

    Repro shape: ``ger log`` before push marks I_new missing; I_old is already on
    Gerrit and cached. After push, the same ``load_changes`` for both must refetch
    I_new — otherwise the overlay shows one line pushed/active and one absent.
    """
    known = "proj~main~Iaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    was_missing = "proj~main~Ibbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    cache = GerritCache(tmp_path / "c.db", web_base="https://g.example")
    monkeypatch.setattr("gerrit_workflow_tools.core.gerrit.cache._now", lambda: 1000)

    cache.load_changes(
        [was_missing],
        probe_updated=lambda _ids: {},
        fetch_changes=lambda _ids: {},
    )
    cache.upsert_changes([_change(known, number=1)])

    fetched: list[list[str]] = []

    def fetch(ids: list[str]) -> dict[str, dict[str, Any]]:
        fetched.append(list(ids))
        return {was_missing: _change(was_missing, number=2)}

    monkeypatch.setattr("gerrit_workflow_tools.core.gerrit.cache._now", lambda: 1005)
    rows = cache.load_changes(
        [known, was_missing],
        probe_updated=lambda _ids: {},
        fetch_changes=fetch,
        trust_window_seconds=10,
    )

    assert fetched == [[was_missing]]
    assert known in rows
    assert was_missing in rows


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
