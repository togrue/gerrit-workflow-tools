"""Tests for triplet-aware Gerrit REST batch/query helpers."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from gerrit_workflow_tools.core.gerrit.rest import (
    GerritApiError,
    alias_batch_fetch_results,
    batch_load_change_details,
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


def test_batch_load_same_change_id_different_branches_no_overwrite() -> None:
    cid = "Iaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    row_main = _change_row(project="p", branch="main", change_id=cid, number=1)
    row_dev = _change_row(project="p", branch="dev", change_id=cid, number=2)
    client = MagicMock()

    def query_changes(q: str, n: int, options: list[str] | None = None) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if "branch:main" in q:
            rows.append(row_main)
        if "branch:dev" in q:
            rows.append(row_dev)
        return rows

    client.query_changes.side_effect = query_changes

    refs = [row_main["id"], row_dev["id"]]
    out = batch_load_change_details(client, refs)

    assert set(out.keys()) == {row_main["id"], row_dev["id"]}
    assert out[row_main["id"]]["_number"] == 1
    assert out[row_dev["id"]]["_number"] == 2


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


def test_resolve_change_ref_triplet_scoped_query() -> None:
    cid = "Ieeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
    assert resolve_change_ref(f"p~main~{cid}") == f"project:p branch:main change:{cid}"


def test_resolve_change_ref_drops_bare_digit_fast_path() -> None:
    assert resolve_change_ref("12345") == "12345"


def test_resolve_change_ref_passthrough_prefixes() -> None:
    assert resolve_change_ref("change:99") == "change:99"
    assert resolve_change_ref("q:status:open") == "status:open"
