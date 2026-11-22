"""Batched change queries over the :class:`GerritRest` seam.

``GerritRest`` makes one round trip. This module composes many of them: OR queries
grouped under a URL budget, halving on oversized requests, per-ref fallback, the
freshness probe and the ``since:`` delta that :class:`GerritCache` drives.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from functools import partial
from typing import Any
from urllib.parse import quote

from gerrit_workflow_tools.core.changeish import Changeish, parse
from gerrit_workflow_tools.core.gerrit.rest import (
    LOG_QUERY_OPTIONS,
    GerritApiError,
    GerritRest,
    GerritUnavailableError,
    parallel_map,
    pick_change_from_query_result,
)


logger = logging.getLogger(__name__)

# Batch OR queries are bounded by what fits in a URL, not by a ref count. Servers commonly
# cap the request line at 8 KB (Jetty's default), so a chunk is grown until its *encoded*
# query would exceed this budget. Measured: latency is flat in chunk size — it is per-request
# overhead, not payload — so bigger chunks are strictly better up to the limit.
DEFAULT_BATCH_QUERY_BYTES = 6000

# Backstop so one pathological stack cannot build an unbounded query even under the budget.
_MAX_BATCH_OR_CHUNK = 300

# HTTP statuses that mean "your request was too big", as opposed to "your request was wrong".
# Only these justify retrying in halves; anything else keeps the per-ref fallback.
_OVERSIZED_REQUEST_STATUSES = frozenset({400, 413, 414, 431})


def _triplet_query(project: str, branch: str, change_id: str) -> str:
    return f"project:{project} branch:{branch} change:{change_id}"


def resolve_change_ref(arg: str) -> str:
    """Build a ``changes/?q=`` query string from a changeish, or pass it through unchanged.

    Query building is Gerrit dialect, so it stays here rather than in the grammar. A
    ``change:``/``cl:`` prefix is already valid Gerrit query syntax and passes through as
    written; anything the grammar does not recognize is handed to Gerrit as typed.
    """
    parsed = parse(arg)
    if parsed.kind == "query":
        return parsed.query or ""
    if parsed.kind == "triplet":
        return _triplet_query(parsed.project or "", parsed.branch or "", parsed.change_id or "")
    if parsed.kind == "change-id":
        return f"change:{parsed.raw}"
    return parsed.raw


def _batch_ref_query(parsed: Changeish) -> str:
    """One-change query for a batch ref, falling back to the ref as written."""
    if parsed.kind == "triplet":
        return _triplet_query(parsed.project or "", parsed.branch or "", parsed.change_id or "")
    batch_ref = parsed.as_batch_ref()
    return f"change:{batch_ref}" if batch_ref else resolve_change_ref(parsed.raw)


def _batch_query_limit(chunk: list[str]) -> int:
    """Allow room for the same Change-Id on multiple branches in one response."""
    return max(len(chunk) * 3 + 10, 25)


def _any_change_query(change_ids: Iterable[str]) -> str:
    return " OR ".join(f"change:{change_id}" for change_id in change_ids)


def _chunk_to_query(chunk: list[str]) -> str:
    """Build one OR query for a chunk: project-scoped Change-Id OR (no branch filter).

    Branch disambiguation is client-side via :func:`alias_batch_fetch_results`.
    When project is unknown (non-triplet refs), fall back to bare ``change:I… OR …``.
    """
    # Dicts rather than lists: one query names each change once, in first-seen order.
    by_project: dict[str, dict[str, None]] = {}
    bare_ids: dict[str, None] = {}
    other_exprs: list[str] = []

    for ref in chunk:
        parsed = parse(ref)
        if parsed.kind == "triplet":
            by_project.setdefault(parsed.project or "", {})[parsed.change_id or ""] = None
            continue
        num = parsed.as_batch_ref()
        if num is None:
            # Not a batch ref at all. Previously this raised GerritApiError, which the caller
            # caught only to degrade the whole chunk to per-change queries.
            other_exprs.append(_batch_ref_query(parsed))
        else:
            bare_ids[num] = None

    group_exprs: list[str] = []
    for project, change_ids in by_project.items():
        inner = _any_change_query(change_ids)
        group_exprs.append(f"project:{project} ({inner})" if len(change_ids) > 1 else f"project:{project} {inner}")
    if bare_ids:
        group_exprs.append(_any_change_query(bare_ids))

    return " OR ".join(group_exprs + other_exprs)


def _ingest_change_rows(out: dict[str, dict[str, Any]], rows: list[Any]) -> None:
    for row in rows:
        if not isinstance(row, dict):
            continue
        triplet = row.get("id")
        if isinstance(triplet, str) and triplet:
            out[triplet] = row


def alias_batch_fetch_results(
    requested: list[str],
    fetched: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Re-key batch results so each *requested* ref resolves to its ChangeInfo.

    Indexes by ``(change_id, branch)`` so duplicate Change-Ids on different
    branches never last-wins. Keeps every fetched row under its Gerrit ``id``
    (including compact ``project~number`` ids) and aliases requested
    ``project~branch~Change-Id`` triplets onto the matching branch row.
    """
    out = dict(fetched)
    by_change_branch: dict[tuple[str, str], dict[str, Any]] = {}
    by_number: dict[int, dict[str, Any]] = {}
    for payload in fetched.values():
        cid = payload.get("change_id")
        branch = payload.get("branch")
        if isinstance(cid, str) and isinstance(branch, str) and branch:
            by_change_branch[(cid, branch)] = payload
        num = payload.get("_number")
        if isinstance(num, int):
            by_number[num] = payload

    for ref in requested:
        if ref in out:
            continue
        parsed = parse(ref)
        match: dict[str, Any] | None = None
        if parsed.kind == "triplet":
            match = by_change_branch.get((parsed.change_id or "", parsed.branch or ""))
        else:
            batch_ref = parsed.as_batch_ref()
            if batch_ref is not None:
                match = by_number.get(int(batch_ref))
        if match is not None:
            out[ref] = match
    return out


def _commit_chunk_to_query(chunk: list[str]) -> str:
    """Build one OR query over commit SHAs."""
    return " OR ".join(f"commit:{sha}" for sha in chunk)


def _encoded_query_len(query: str) -> int:
    """Bytes *query* occupies in a URL, which is what a server's request-line limit counts."""
    return len(quote(query))


def _chunk_by_query_budget(
    refs: list[str],
    build_query: Callable[[list[str]], str],
    *,
    budget_bytes: int = DEFAULT_BATCH_QUERY_BYTES,
    max_refs: int = _MAX_BATCH_OR_CHUNK,
) -> list[list[str]]:
    """Split *refs* into the fewest chunks whose built query fits the URL budget.

    Bisects rather than growing one ref at a time: the common case (everything fits) costs
    a single query build, and a stack that does not fit costs ``O(log n)`` builds instead of
    ``O(n)``. A single ref is never split further — if one ref alone blows the budget, that
    is the server's call to make, not ours.
    """
    if not refs:
        return []
    if len(refs) <= max_refs and _encoded_query_len(build_query(refs)) <= budget_bytes:
        return [refs]
    if len(refs) == 1:
        return [refs]
    mid = len(refs) // 2
    return _chunk_by_query_budget(
        refs[:mid], build_query, budget_bytes=budget_bytes, max_refs=max_refs
    ) + _chunk_by_query_budget(refs[mid:], build_query, budget_bytes=budget_bytes, max_refs=max_refs)


def _query_batch_with_halving(
    client: GerritRest,
    chunk: list[str],
    *,
    build_query: Callable[[list[str]], str],
    leaf_rows: Callable[[str], list[dict[str, Any]]],
    opts: list[str],
) -> list[dict[str, Any]]:
    """Run one batched OR query, halving the chunk when the server says it was too big.

    The byte budget is an estimate of a limit only the server knows, so a chunk can still be
    refused. Answering that by querying every ref individually — what this used to do —
    turns one oversized request into ``N`` sequential ones, which is slower than the batching
    it replaces. Halving finds the size the server accepts in ``O(log n)`` requests instead.

    Errors that are not about size keep the per-ref fallback: they are per-change problems
    (a deleted change, a permission gap) that splitting cannot fix. An unreachable Gerrit,
    or one that rejects the credentials, is no per-change problem: it propagates, once.
    """
    try:
        return client.query_changes(build_query(chunk), n=_batch_query_limit(chunk), options=opts)
    except GerritUnavailableError:
        raise
    except GerritApiError as error:
        if len(chunk) > 1 and error.status in _OVERSIZED_REQUEST_STATUSES:
            mid = len(chunk) // 2
            logger.warning("Gerrit refused a %d-ref batch as oversized (%s); retrying in halves", len(chunk), error)
            halves = (chunk[:mid], chunk[mid:])
            return [
                row
                for half in halves
                for row in _query_batch_with_halving(
                    client, half, build_query=build_query, leaf_rows=leaf_rows, opts=opts
                )
            ]
        logger.warning("batched Gerrit query failed (%s), falling back per change", error)
        return [row for ref in chunk for row in leaf_rows(ref)]


def _one_change_rows(client: GerritRest, ref: str) -> list[dict[str, Any]]:
    """Rows for a single ref, for use as the leaf of a failed batch."""
    try:
        one = query_single_change(client, ref)
    except GerritUnavailableError:
        raise
    except GerritApiError as error:
        logger.warning("Gerrit query failed for %s: %s", ref, error)
        return []
    return [one] if one else []


def _query_in_chunks(
    client: GerritRest,
    refs: Iterable[str],
    *,
    build_query: Callable[[list[str]], str],
    leaf_rows: Callable[[str], list[dict[str, Any]]],
    opts: list[str],
) -> dict[str, dict[str, Any]]:
    """Map Gerrit ``id`` to ChangeInfo for *refs*, batched in parallel under the URL budget.

    An empty chunk result means none of its refs exist, so it takes no per-ref fallback.
    """
    chunks = _chunk_by_query_budget(list(dict.fromkeys(refs)), build_query)
    out: dict[str, dict[str, Any]] = {}
    jobs = [
        partial(_query_batch_with_halving, client, chunk, build_query=build_query, leaf_rows=leaf_rows, opts=opts)
        for chunk in chunks
    ]
    for rows in parallel_map(jobs):
        _ingest_change_rows(out, rows)
    return out


def _query_change_refs(client: GerritRest, refs: list[str], opts: list[str]) -> dict[str, dict[str, Any]]:
    return _query_in_chunks(
        client, refs, build_query=_chunk_to_query, leaf_rows=partial(_one_change_rows, client), opts=opts
    )


def query_single_change(client: GerritRest, ref: str) -> dict[str, Any] | None:
    """Query one Gerrit change by triplet or numeric id; raise on ambiguous multi-match.

    ``None`` means Gerrit answered and has no such change. A failed query raises.
    """
    rows = client.query_changes(_batch_ref_query(parse(ref)), n=5, options=list(LOG_QUERY_OPTIONS))
    if not rows:
        return None
    return pick_change_from_query_result(rows)


def change_freshness_key(payload: dict[str, Any]) -> str | None:
    """Token that changes when NoteDb follow-ups (comments, checks) may have moved.

    ``ChangeInfo.updated`` is often second-precision. A comment reply or resolve in the
    same second leaves it unchanged, so it is not a complete validity key. Prefer
    ``meta_rev_id`` (the NoteDb meta SHA). Fall back to ``updated`` plus comment counts
    when the probe row has no meta SHA.
    """
    meta = payload.get("meta_rev_id")
    if isinstance(meta, str) and meta:
        return meta
    updated = payload.get("updated")
    if not isinstance(updated, str) or not updated:
        return None
    parts = [updated]
    for field in ("unresolved_comment_count", "total_comment_count"):
        raw = payload.get(field)
        if isinstance(raw, int):
            parts.append(f"{field}={raw}")
    return "|".join(parts)


def probe_changes_updated(client: GerritRest, refs: list[str]) -> dict[str, str]:
    """Return freshness tokens keyed by requested refs (and payload ids).

    Tokens come from :func:`change_freshness_key`, not raw ``updated`` timestamps.
    Composes round trips (chunk, parallelise, alias) over :class:`GerritRest`, so it lives
    above the seam rather than on it — every implementation would otherwise have to repeat
    this. Mirrors :func:`batch_load_change_details`.
    """
    aliased = alias_batch_fetch_results(refs, _query_change_refs(client, refs, ["SKIP_DIFFSTAT"]))
    return {key: token for key, payload in aliased.items() if (token := change_freshness_key(payload))}


def batch_load_change_details(client: GerritRest, refs: list[str]) -> dict[str, dict[str, Any]]:
    """Map Gerrit ``id`` to ChangeInfo using project-scoped Change-Id OR chunks.

    Branch filtering is not applied in the query; callers use
    :func:`alias_batch_fetch_results` to bind requested target-branch triplets.
    """
    return _query_change_refs(client, refs, list(LOG_QUERY_OPTIONS))


def batch_load_changes_by_commit(
    client: GerritRest,
    shas: list[str],
    *,
    options: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Map Gerrit ``id`` to ChangeInfo for commits, via batched ``commit:<sha> OR …``.

    Used by inbox chain assembly to resolve parent SHAs that were not in the
    original query result. Two round trips for the whole inbox: the section
    query, then this follow-up — never a per-chain relation call.
    """
    opts = list(options) if options is not None else list(LOG_QUERY_OPTIONS)

    def _one_commit_rows(sha: str) -> list[dict[str, Any]]:
        try:
            return client.query_changes(f"commit:{sha}", n=5, options=opts)
        except GerritUnavailableError:
            raise
        except GerritApiError as error:
            logger.warning("Gerrit query failed for commit:%s: %s", sha, error)
            return []

    keys = (raw.strip().lower() for raw in shas)
    return _query_in_chunks(
        client, filter(None, keys), build_query=_commit_chunk_to_query, leaf_rows=_one_commit_rows, opts=opts
    )
