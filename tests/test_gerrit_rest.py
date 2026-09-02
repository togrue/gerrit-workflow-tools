"""Tests for triplet-aware Gerrit REST batch/query helpers."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock
from urllib.parse import quote

import pytest

from gerrit_workflow_tools.core.gerrit.rest import (
    GerritApiError,
    HttpGerritRest,
    _chunk_by_query_budget,
    _chunk_to_query,
    alias_batch_fetch_results,
    batch_load_change_details,
    encode_gerrit_path_segment,
    pick_change_from_query_result,
    query_single_change,
    resolve_change_ref,
)


def _change_row(
    *,
    project: str,
    branch: str,
    change_id: str,
    number: int,
) -> dict[str, Any]:
    return {
        "id": f"{project}~{branch}~{change_id}",
        "change_id": change_id,
        "project": project,
        "branch": branch,
        "_number": number,
        "subject": "subj",
    }


def test_encode_gerrit_path_segment_encodes_slashes_once() -> None:
    assert encode_gerrit_path_segment("tools/SomeProject~59446") == "tools%2FSomeProject~59446"


def test_encode_gerrit_path_segment_does_not_double_encode() -> None:
    """Gerrit ChangeInfo.id values are already percent-encoded."""
    assert encode_gerrit_path_segment("tools%2FSomeProject~59446") == "tools%2FSomeProject~59446"
    assert "%252F" not in encode_gerrit_path_segment("tools%2FSomeProject~59446")


def test_get_messages_uses_single_encoded_project_path() -> None:
    """Follow-ups pass Gerrit's compact id; REST paths must not double-encode."""
    from gerrit_workflow_tools.core.gerrit.rest import GerritAuth

    client = HttpGerritRest("https://g.example", auth=GerritAuth(user="u", secret="s"))
    paths: list[str] = []

    def _capture(path: str, *, method: str = "GET", params=None, json_body=None) -> list[dict[str, Any]]:
        del method, params, json_body
        paths.append(path)
        return []

    client._request_json = _capture  # type: ignore[method-assign]
    client.get_messages("tools%2FSomeProject~59446")

    assert paths == ["changes/tools%2FSomeProject~59446/messages"]


def test_alias_batch_fetch_results_maps_compact_gerrit_id_to_requested_triplet() -> None:
    cid = "Iaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    requested = f"p~feature~{cid}"
    compact = "p~123"
    payload = {
        "id": compact,
        "change_id": cid,
        "branch": "feature",
        "_number": 123,
    }
    out = alias_batch_fetch_results([requested], {compact: payload})
    assert out[compact] is payload
    assert out[requested] is payload


def test_alias_batch_fetch_results_branch_aware_no_last_wins() -> None:
    cid = "Iaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    row_main = _change_row(project="p", branch="main", change_id=cid, number=1)
    row_dev = _change_row(project="p", branch="dev", change_id=cid, number=2)
    # Last payload in dict would win under change_id-only indexing.
    fetched = {row_dev["id"]: row_dev, row_main["id"]: row_main}
    requested_main = row_main["id"]
    requested_dev = row_dev["id"]
    out = alias_batch_fetch_results([requested_main, requested_dev], fetched)
    assert out[requested_main]["_number"] == 1
    assert out[requested_dev]["_number"] == 2


def test_alias_skips_other_branch_when_only_target_requested() -> None:
    cid = "Iaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    row_main = _change_row(project="p", branch="main", change_id=cid, number=1)
    row_dev = _change_row(project="p", branch="dev", change_id=cid, number=2)
    requested = row_dev["id"]
    out = alias_batch_fetch_results([requested], {row_main["id"]: row_main, row_dev["id"]: row_dev})
    assert out[requested] is row_dev
    assert out[row_main["id"]] is row_main


def test_batch_load_same_change_id_different_branches_no_overwrite() -> None:
    cid = "Iaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    row_main = _change_row(project="p", branch="main", change_id=cid, number=1)
    row_dev = _change_row(project="p", branch="dev", change_id=cid, number=2)
    client = MagicMock()

    def query_changes(q: str, n: int, options: list[str] | None = None) -> list[dict[str, Any]]:
        del n, options
        assert "branch:" not in q
        assert f"change:{cid}" in q
        return [row_main, row_dev]

    client.query_changes.side_effect = query_changes

    refs = [row_main["id"], row_dev["id"]]
    out = batch_load_change_details(client, refs)

    assert set(out.keys()) == {row_main["id"], row_dev["id"]}
    assert out[row_main["id"]]["_number"] == 1
    assert out[row_dev["id"]]["_number"] == 2
    assert client.query_changes.call_count == 1


def test_query_single_change_triplet_returns_one() -> None:
    cid = "Ibbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    row = _change_row(project="p", branch="main", change_id=cid, number=10)
    client = MagicMock()
    client.query_changes.return_value = [row]

    got = query_single_change(client, row["id"])

    assert got == row
    client.query_changes.assert_called_once()
    q = client.query_changes.call_args.args[0]
    assert q == f"project:p branch:main change:{cid}"


def test_query_single_change_multi_match_raises() -> None:
    cid = "Icccccccccccccccccccccccccccccccccccccccc"
    rows = [
        _change_row(project="p", branch="main", change_id=cid, number=11),
        _change_row(project="p", branch="dev", change_id=cid, number=12),
    ]
    client = MagicMock()
    client.query_changes.return_value = rows

    with pytest.raises(GerritApiError, match="ambiguous"):
        query_single_change(client, f"p~main~{cid}")

    with pytest.raises(GerritApiError, match="ambiguous"):
        pick_change_from_query_result(rows)


def test_case_sensitive_change_id_keys_in_batch() -> None:
    upper = "I" + "A" * 40
    lower = "I" + "a" * 40
    row_upper = _change_row(project="p", branch="main", change_id=upper, number=20)
    row_lower = _change_row(project="p", branch="main", change_id=lower, number=21)
    client = MagicMock()

    def query_changes(q: str, n: int, options: list[str] | None = None) -> list[dict[str, Any]]:
        del n, options
        rows: list[dict[str, Any]] = []
        if f"change:{upper}" in q:
            rows.append(row_upper)
        if f"change:{lower}" in q:
            rows.append(row_lower)
        return rows

    client.query_changes.side_effect = query_changes

    out = batch_load_change_details(client, [row_upper["id"], row_lower["id"]])

    assert set(out.keys()) == {row_upper["id"], row_lower["id"]}
    assert out[row_upper["id"]]["_number"] == 20
    assert out[row_lower["id"]]["_number"] == 21


def test_chunk_to_query_groups_same_project_no_branch() -> None:
    cid_a = "Iaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    cid_b = "Ibbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    chunk = [
        f"p~dev~{cid_a}",
        f"p~main~{cid_b}",
    ]
    q = _chunk_to_query(chunk)
    assert q == f"project:p (change:{cid_a} OR change:{cid_b})"
    assert "branch:" not in q


def test_chunk_to_query_dedupes_same_change_id_across_branches() -> None:
    cid = "Iaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    chunk = [f"p~main~{cid}", f"p~dev~{cid}"]
    q = _chunk_to_query(chunk)
    assert q == f"project:p change:{cid}"
    assert q.count(f"change:{cid}") == 1


def test_batch_load_uses_chunked_queries_not_per_change() -> None:
    """A 30-change stack fits one batch query, and must never be 30 individual ones."""
    refs = [f"p~dev~I{format(i, '040x')}" for i in range(30)]
    row = _change_row(project="p", branch="dev", change_id=refs[0].split("~")[-1], number=1)
    client = MagicMock()

    def query_changes(q: str, n: int, options: list[str] | None = None) -> list[dict[str, Any]]:
        del n, options
        if q.startswith("project:p (change:"):
            return [row]
        return []

    client.query_changes.side_effect = query_changes

    batch_load_change_details(client, refs)

    assert client.query_changes.call_count == 1
    first_q = client.query_changes.call_args_list[0].args[0]
    assert first_q.startswith("project:p (change:")
    assert "branch:" not in first_q


def test_batch_load_empty_does_not_fallback_per_change() -> None:
    """Unknown Change-Ids returning [] must not trigger N individual queries."""
    refs = [f"p~dev~I{format(i, '040x')}" for i in range(5)]
    client = MagicMock()
    client.query_changes.return_value = []

    out = batch_load_change_details(client, refs)

    assert out == {}
    assert client.query_changes.call_count == 1


def test_batch_load_api_error_falls_back_per_change() -> None:
    refs = [f"p~dev~I{format(i, '040x')}" for i in range(2)]
    row = _change_row(project="p", branch="dev", change_id=refs[0].split("~")[-1], number=1)
    client = MagicMock()
    calls: list[str] = []

    def query_changes(q: str, n: int, options: list[str] | None = None) -> list[dict[str, Any]]:
        del n, options
        calls.append(q)
        if q.startswith("project:p ("):
            raise GerritApiError("query too large")
        if q.startswith("project:p branch:dev change:"):
            return [row]
        return []

    client.query_changes.side_effect = query_changes

    out = batch_load_change_details(client, refs)

    assert out.get(row["id"]) == row or any(v.get("_number") == 1 for v in out.values())
    assert any(q.startswith("project:p (") for q in calls)
    assert sum(1 for q in calls if "branch:dev change:" in q) == 2


def test_resolve_change_ref_triplet_scoped_query() -> None:
    cid = "Ieeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
    assert resolve_change_ref(f"p~main~{cid}") == f"project:p branch:main change:{cid}"


def test_resolve_change_ref_drops_bare_digit_fast_path() -> None:
    assert resolve_change_ref("12345") == "12345"


def test_resolve_change_ref_passthrough_prefixes() -> None:
    assert resolve_change_ref("change:99") == "change:99"
    assert resolve_change_ref("q:status:open") == "status:open"


def _refs(n: int, project: str = "p") -> list[str]:
    return [f"{project}~dev~I{format(i, '040x')}" for i in range(n)]


def test_chunking_is_bounded_by_url_bytes_not_by_a_ref_count() -> None:
    """Chunks grow until the encoded query would exceed the budget."""
    chunks = _chunk_by_query_budget(_refs(200), _chunk_to_query, budget_bytes=6000)

    assert len(chunks) > 1, "200 refs must not fit one 6 KB query"
    assert sum(len(c) for c in chunks) == 200
    assert [r for c in chunks for r in c] == _refs(200), "order and membership preserved"
    for chunk in chunks:
        assert len(quote(_chunk_to_query(chunk))) <= 6000


def test_a_stack_that_fits_is_one_chunk() -> None:
    """The common case costs a single query build and a single request."""
    assert _chunk_by_query_budget(_refs(100), _chunk_to_query, budget_bytes=6000) == [_refs(100)]


def test_a_single_ref_is_never_split_even_when_over_budget() -> None:
    """One ref is indivisible; whether it is too long is the server's call."""
    assert _chunk_by_query_budget(_refs(1), _chunk_to_query, budget_bytes=1) == [_refs(1)]


def test_oversized_batch_retries_in_halves_instead_of_per_change() -> None:
    """A 414 means "too big", so halve — never fan out to one query per ref."""
    refs = _refs(32)
    accepted: list[str] = []

    def query_changes(q: str, n: int, options: list[str] | None = None) -> list[dict[str, Any]]:
        del n, options
        if q.count(" OR ") + 1 > 8:
            raise GerritApiError("URI too long", status=414)
        accepted.append(q)
        return []

    client = MagicMock()
    client.query_changes.side_effect = query_changes

    batch_load_change_details(client, refs)

    # 32 refused -> two 16s refused -> four 8s accepted: 1 + 2 + 4 = 7 requests.
    # The old behaviour was 1 refused batch plus 32 sequential single-ref queries.
    assert client.query_changes.call_count == 7
    assert len(accepted) == 4
    assert all(q.startswith("project:p (change:") for q in accepted), "leaves are still batches"


def test_a_non_size_error_keeps_the_per_change_fallback() -> None:
    """A 500 is a per-change problem; splitting cannot fix it, so do not double the work."""
    refs = _refs(4)

    def query_changes(q: str, n: int, options: list[str] | None = None) -> list[dict[str, Any]]:
        del n, options
        if " OR " in q:
            raise GerritApiError("boom", status=500)
        return []

    client = MagicMock()
    client.query_changes.side_effect = query_changes

    batch_load_change_details(client, refs)

    # One refused batch, then exactly one query per ref — no halving generations.
    assert client.query_changes.call_count == 1 + len(refs)
