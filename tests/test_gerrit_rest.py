"""Tests for triplet-aware Gerrit REST batch/query helpers."""

from __future__ import annotations

import io
from typing import Any
from unittest.mock import MagicMock
from urllib.error import HTTPError
from urllib.parse import quote

import pytest

from gerrit_workflow_tools.core.gerrit.change_queries import (
    _chunk_by_query_budget,
    _chunk_to_query,
    alias_batch_fetch_results,
    batch_load_change_details,
    query_single_change,
    resolve_change_ref,
)
from gerrit_workflow_tools.core.gerrit.http_rest import HttpGerritRest
from gerrit_workflow_tools.core.gerrit.rest import (
    GerritApiError,
    encode_gerrit_path_segment,
    pick_change_from_query_result,
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


def test_encode_gerrit_path_segment_does_not_double_encode() -> None:
    """Gerrit ChangeInfo.id values are already percent-encoded."""
    assert encode_gerrit_path_segment("tools%2FSomeProject~59446") == "tools%2FSomeProject~59446"
    assert "%252F" not in encode_gerrit_path_segment("tools%2FSomeProject~59446")


def test_get_messages_uses_single_encoded_project_path() -> None:
    """Follow-ups pass Gerrit's compact id; REST paths must not double-encode."""
    from gerrit_workflow_tools.core.gerrit.credentials import GerritAuth

    client = HttpGerritRest("https://g.example", auth=GerritAuth(user="u", secret="s"))
    paths: list[str] = []

    def _capture(path: str, *, method: str = "GET", params=None, json_body=None) -> list[dict[str, Any]]:
        del method, params, json_body
        paths.append(path)
        return []

    client._request_json = _capture  # type: ignore[method-assign]
    client.get_messages("tools%2FSomeProject~59446")

    assert paths == ["changes/tools%2FSomeProject~59446/messages"]


def test_get_change_requests_the_batch_row_options() -> None:
    """``/detail`` has no ``current_revision``; ChangeStore.get_change assumes these options."""
    from gerrit_workflow_tools.core.gerrit.credentials import GerritAuth
    from gerrit_workflow_tools.core.gerrit.rest import LOG_QUERY_OPTIONS

    client = HttpGerritRest("https://g.example", auth=GerritAuth(user="u", secret="s"))
    captured: list[tuple[str, object]] = []

    def _capture(path: str, *, method: str = "GET", params=None, json_body=None) -> dict[str, Any]:
        del method, json_body
        captured.append((path, params))
        return {"_number": 1}

    client._request_json = _capture  # type: ignore[method-assign]
    client.get_change("proj~main~I" + "a" * 40)

    assert captured == [(f"changes/proj~main~I{'a' * 40}", [("o", opt) for opt in LOG_QUERY_OPTIONS])]


def test_get_comments_enable_context_query_params() -> None:
    from gerrit_workflow_tools.core.gerrit.credentials import GerritAuth

    client = HttpGerritRest("https://g.example", auth=GerritAuth(user="u", secret="s"))
    captured: list[tuple[str, object]] = []

    def _capture(path: str, *, method: str = "GET", params=None, json_body=None) -> dict[str, Any]:
        del method, json_body
        captured.append((path, params))
        return {}

    client._request_json = _capture  # type: ignore[method-assign]
    client.get_comments("proj~main~Iaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", enable_context=True, context_padding=2)
    client.get_comments("proj~main~Iaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")

    assert captured[0] == (
        "changes/proj~main~Iaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/comments",
        {"enable-context": "true", "context-padding": "2"},
    )
    assert captured[1] == ("changes/proj~main~Iaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/comments", None)


def test_set_reviewers_batch_confirms_reviewers_on_the_review_request() -> None:
    """Set Review rejects the whole body with HTTP 400 unless large groups are confirmed."""

    from gerrit_workflow_tools.core.gerrit.credentials import GerritAuth

    client = HttpGerritRest("https://g.example", auth=GerritAuth(user="u", secret="s"))
    calls: list[tuple[str, object]] = []

    def _capture(path: str, *, method: str = "GET", params=None, json_body=None) -> dict[str, Any]:
        del method, params
        calls.append((path, json_body))
        return {}

    client._request_json = _capture  # type: ignore[method-assign]
    cid = "I" + "b" * 40
    client.set_reviewers_batch(f"proj~main~{cid}", reviewers=["alice"], ccs=["bob"])
    assert calls == [
        (
            f"changes/proj~main~{cid}/revisions/current/review",
            {
                "reviewers": [
                    {"reviewer": "alice", "confirmed": True},
                    {"reviewer": "bob", "state": "CC", "confirmed": True},
                ]
            },
        )
    ]


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


def test_ambiguous_query_says_when_the_list_is_cut_short() -> None:
    rows: list[dict[str, Any]] = [{"_number": n} for n in range(1, 26)]
    rows[-1]["_more_changes"] = True
    with pytest.raises(GerritApiError) as raised:
        pick_change_from_query_result(rows)
    assert "(25+ matches): 1, 2, 3, 4, 5, ..." in str(raised.value)


def _capturing_client() -> tuple[HttpGerritRest, list[tuple[str, str, object]]]:
    from gerrit_workflow_tools.core.gerrit.credentials import GerritAuth

    client = HttpGerritRest("https://g.example", auth=GerritAuth(user="u", secret="s"))
    calls: list[tuple[str, str, object]] = []

    def _capture(path: str, *, method: str = "GET", params=None, json_body=None) -> dict[str, Any]:
        del params
        calls.append((method, path, json_body))
        return {}

    client._request_json = _capture  # type: ignore[method-assign]
    return client, calls


def test_publish_drafts_sends_votes_with_the_drafts() -> None:
    client, calls = _capturing_client()
    client.publish_drafts("proj~main~I" + "c" * 40, message="lgtm", labels={"Code-Review": 1})
    assert calls == [
        (
            "POST",
            f"changes/proj~main~I{'c' * 40}/revisions/current/review",
            {"drafts": "PUBLISH_ALL_REVISIONS", "message": "lgtm", "labels": {"Code-Review": 1}},
        )
    ]


def test_get_related_asks_for_the_relation_chain_of_one_patchset() -> None:
    client, calls = _capturing_client()
    client.get_related("proj~main~I" + "c" * 40, 3)
    assert calls == [("GET", f"changes/proj~main~I{'c' * 40}/revisions/3/related", None)]


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (b"nosuchuser does not identify a  registered user\n", "nosuchuser does not identify a registered user"),
        (  # what Gerrit 3.14 answers to an unknown reviewer
            b')]}\'\n{"error": "error adding reviewer", "reviewers": {"nosuchuser": {"input": "nosuchuser",'
            b' "error": "Account \'nosuchuser\' not found\\nnosuchuser does not identify a registered user or group"'
            b"}}}",
            "Account 'nosuchuser' not found; nosuchuser does not identify a registered user or group",
        ),
        (
            b')]}\'\n{"reviewers": {"nosuchuser": {"input": "nosuchuser", "error": "nosuchuser is not a user"},'
            b' "alice": {"input": "alice"}}}',
            "nosuchuser is not a user",
        ),
        (b"", "Bad Request"),
    ],
)
def test_an_http_error_says_gerrits_own_reason_on_one_line(
    body: bytes, reason: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gerrit_workflow_tools.core.gerrit import http_rest
    from gerrit_workflow_tools.core.gerrit.credentials import GerritAuth

    def _urlopen(request: Any, **_kwargs: object) -> Any:
        raise HTTPError(request.full_url, 400, "Bad Request", {}, io.BytesIO(body))  # type: ignore[arg-type]

    monkeypatch.setattr(http_rest, "urlopen", _urlopen)
    client = HttpGerritRest("https://g.example", auth=GerritAuth(user="u", secret="s"))
    with pytest.raises(GerritApiError) as caught:
        client.set_reviewers_batch("p~main~I" + "a" * 40, reviewers=["nosuchuser"])
    assert str(caught.value) == f"Gerrit HTTP 400: {reason}"
    assert (caught.value.status, caught.value.reason) == (400, reason)


@pytest.mark.parametrize("bare", ["I" + "a" * 40, "i" + "a" * 40, "Iabc", "not-a-change"])
def test_a_bare_change_id_never_reaches_a_changes_url(bare: str) -> None:
    """``/changes/{id}`` takes a triplet, a Gerrit id or a number: a bare Change-Id is refused before Gerrit.

    Gerrit answers a bare Change-Id with 404 ("Multiple changes found") once it is on a second
    branch (GitHub #31), so the URL seam refuses it outright, whatever the caller.
    """
    from gerrit_workflow_tools.core.gerrit.credentials import GerritAuth
    from gerrit_workflow_tools.core.gerrit.rest import BareChangeIdError

    client = HttpGerritRest("https://g.example", auth=GerritAuth(user="u", secret="s"))
    client._request_json = lambda *_a, **_k: pytest.fail("asked Gerrit by a bare Change-Id")  # type: ignore[method-assign]
    for call in (
        lambda: client.get_change(bare),
        lambda: client.get_comments(bare),
        lambda: client.suggest_change_reviewers(bare, query="al"),
        lambda: client.put_draft(bare, 1, {"path": "a", "message": "m"}),
    ):
        with pytest.raises(BareChangeIdError, match="bare Change-Id"):
            call()


@pytest.mark.parametrize("ref", ["12345", "proj~main~I" + "a" * 40, "tools%2FSomeProject~59446"])
def test_a_triplet_gerrit_id_or_number_reaches_its_changes_url(ref: str) -> None:
    from gerrit_workflow_tools.core.gerrit.credentials import GerritAuth

    client = HttpGerritRest("https://g.example", auth=GerritAuth(user="u", secret="s"))
    paths: list[str] = []

    def record(path: str, **_k: object) -> list[object]:
        paths.append(path)
        return []

    client._request_json = record  # type: ignore[method-assign,assignment]
    client.get_messages(ref)
    assert paths == [f"changes/{encode_gerrit_path_segment(ref)}/messages"]


def test_gerrit_text_reaches_core_without_terminal_control_characters(monkeypatch: pytest.MonkeyPatch) -> None:
    """A commenter's escape sequences (OSC 52 clipboard write, a forged colour) never leave the seam.

    CRLF and a lone CR become ``\\n``, so a Windows-typed comment keeps its lines and a CR cannot
    overwrite one; ``\\t`` stays; C0, DEL and C1 controls go. File paths are identities and stay
    as Gerrit sent them: two files whose names differ by a control character keep their comments.
    """
    from gerrit_workflow_tools.core.gerrit import http_rest
    from gerrit_workflow_tools.core.gerrit.credentials import GerritAuth

    payload = (
        b')]}\'\n{"src/a\\u001b.py": [{"id": "c1", "path": "src/a\\u001b.py", "author": {"name": "Mallory\\u009b2J"},'
        b' "message": "LGTM\\u001b]52;c;cm0gLXJmIH4=\\u0007\\r\\nsee\\tline 2\\rhidden\\u007f\\u001b[31m"}],'
        b' "src/a.py": [{"id": "c2", "message": "plain"}]}'
    )

    class _Response(io.BytesIO):
        def __enter__(self) -> _Response:
            return self

    monkeypatch.setattr(http_rest, "urlopen", lambda *_a, **_k: _Response(payload))
    client = HttpGerritRest("https://g.example", auth=GerritAuth(user="u", secret="s"))

    comments = client.get_comments("p~main~I" + "a" * 40)

    assert comments == {
        "src/a\x1b.py": [
            {
                "id": "c1",
                "path": "src/a\x1b.py",
                "author": {"name": "Mallory2J"},
                "message": "LGTM]52;c;cm0gLXJmIH4=\nsee\tline 2\nhidden[31m",
            }
        ],
        "src/a.py": [{"id": "c2", "message": "plain"}],
    }


def test_gerrits_error_reason_carries_no_terminal_control_characters(monkeypatch: pytest.MonkeyPatch) -> None:
    from gerrit_workflow_tools.core.gerrit import http_rest
    from gerrit_workflow_tools.core.gerrit.credentials import GerritAuth

    def _urlopen(request: Any, **_kwargs: object) -> Any:
        body = io.BytesIO(b"\x1b]0;pwned\x07no such\x1b[2K change")
        raise HTTPError(request.full_url, 404, "Not Found", {}, body)  # type: ignore[arg-type]

    monkeypatch.setattr(http_rest, "urlopen", _urlopen)
    client = HttpGerritRest("https://g.example", auth=GerritAuth(user="u", secret="s"))
    with pytest.raises(GerritApiError) as caught:
        client.get_messages("p~main~I" + "a" * 40)
    assert caught.value.reason == "]0;pwnedno such[2K change"
