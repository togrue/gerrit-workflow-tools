"""Shared Gerrit change status for local commits (used by ``git glog`` and ``git gshow``)."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from gerrit_workflow_tools.gerrit_client import GerritApiError, GerritClient

logger = logging.getLogger(__name__)

# Batched change query: labels + submittable + revisions in one round trip (no separate /detail).
GLOG_QUERY_OPTIONS = (
    "DETAILED_LABELS",
    "SUBMITTABLE",
    "CURRENT_REVISION",
    "ALL_REVISIONS",
)
_BATCH_OR_CHUNK = 25
_PARALLEL_IO = 8


@dataclass
class GlogCommit:
    sha: str
    short_sha: str
    summary: str
    change_id: str | None
    pushed: bool  # True if a Gerrit change exists for this Change-Id (any patchset state)
    patchset_status: str  # "active" | "newer" | "outdated" | "absent"
    verified: int | None  # -1, 0, +1; None = no vote
    code_review: int | None  # -2, -1, 0, +1, +2; None = no vote
    comments_unresolved: int
    ci_failures: list[str] = field(default_factory=list)
    gerrit_url: str | None = None
    submittable: bool = False
    attention_reasons: list[str] = field(default_factory=list)


def extract_label_value(labels: dict[str, Any], label_name: str) -> int | None:
    """Return the effective vote value for a Gerrit label, or None if no vote."""
    label = labels.get(label_name)
    if not isinstance(label, dict):
        return None

    v = label.get("value")
    if v is not None:
        try:
            iv = int(v)
            if iv == 0:
                all_vals = [
                    int(vote.get("value", 0))
                    for vote in label.get("all", [])
                    if isinstance(vote, dict) and vote.get("value") is not None
                ]
                if not any(x != 0 for x in all_vals):
                    return None
            return iv
        except (TypeError, ValueError):
            pass

    all_vals = [
        int(vote.get("value", 0))
        for vote in label.get("all", [])
        if isinstance(vote, dict) and vote.get("value") is not None
    ]
    if all_vals:
        max_val = max(all_vals, default=0)
        if max_val == 0 and not any(x != 0 for x in all_vals):
            return None
        return max_val

    return None


def count_unresolved_in_file_map(file_map: dict[str, list[dict[str, Any]]]) -> int:
    count = 0
    for comments in file_map.values():
        for c in comments:
            if isinstance(c, dict) and c.get("unresolved") is True:
                count += 1
    return count


def norm_change_id(change_id: str) -> str:
    return change_id.lower()


def norm_sha(sha: str) -> str:
    return sha.strip().lower()


def patchset_status(local_sha: str, detail: dict[str, Any]) -> str:
    """Compare local commit SHA to Gerrit ``current_revision`` / ``revisions``."""
    ls = norm_sha(local_sha)
    cur = detail.get("current_revision")
    cur_n = norm_sha(cur) if isinstance(cur, str) else None
    revs = detail.get("revisions")
    rev_keys: set[str] = set()
    if isinstance(revs, dict):
        for k in revs:
            if isinstance(k, str):
                rev_keys.add(norm_sha(k))
    if cur_n is None and len(rev_keys) == 1:
        cur_n = next(iter(rev_keys))
    if cur_n and ls == cur_n:
        return "active"
    if rev_keys and ls in rev_keys and cur_n and ls != cur_n:
        return "outdated"
    if cur_n or rev_keys:
        return "newer"
    return "newer"


def count_unresolved_via_comments(client: GerritClient, api_change_id: str) -> int:
    try:
        file_map = client.get_comments(api_change_id)
        return count_unresolved_in_file_map(file_map)
    except GerritApiError as e:
        logger.warning("Gerrit comments failed for %s: %s", api_change_id, e)
        return 0


def batch_load_change_details(client: GerritClient, change_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Map normalized Change-Id to ChangeInfo using chunked ``change:I1 OR change:I2`` queries."""
    out: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    unique: list[str] = []
    for cid in change_ids:
        k = norm_change_id(cid)
        if k not in seen:
            seen.add(k)
            unique.append(cid)

    opts = list(GLOG_QUERY_OPTIONS)
    for i in range(0, len(unique), _BATCH_OR_CHUNK):
        chunk = unique[i : i + _BATCH_OR_CHUNK]
        q = " OR ".join(f"change:{c}" for c in chunk)
        try:
            rows = client.query_changes(q, n=len(chunk) + 10, options=opts)
        except GerritApiError as e:
            logger.warning("batched Gerrit query failed (%s), falling back per change", e)
            for c in chunk:
                one = query_single_change(client, c)
                if one:
                    raw_id = one.get("change_id")
                    if isinstance(raw_id, str):
                        out[norm_change_id(raw_id)] = one
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            raw_id = row.get("change_id")
            if isinstance(raw_id, str):
                out[norm_change_id(raw_id)] = row
    return out


def query_single_change(client: GerritClient, change_id: str) -> dict[str, Any] | None:
    try:
        rows = client.query_changes(f"change:{change_id}", n=5, options=list(GLOG_QUERY_OPTIONS))
    except GerritApiError as e:
        logger.warning("Gerrit query failed for %s: %s", change_id, e)
        return None
    if not rows:
        return None
    return rows[0]


def gerrit_change_url(web_base: str, change: dict[str, Any]) -> str | None:
    proj = change.get("project")
    num = change.get("_number")
    if not proj or not isinstance(num, int):
        return None
    proj_enc = quote(str(proj), safe="")
    return f"{web_base}/c/{proj_enc}/+/{num}"


def fetch_check_failures(client: GerritClient, change_id: str) -> list[str]:
    """Attempt to retrieve failed CI check names via the Gerrit Checks API."""
    enc = quote(change_id, safe="")
    try:
        data = client._request_json(f"changes/{enc}/revisions/current/checks")
    except GerritApiError:
        return []
    if not isinstance(data, list):
        return []
    failed: list[str] = []
    for check in data:
        if not isinstance(check, dict):
            continue
        if check.get("state") == "FAILED":
            name = check.get("checker_name") or check.get("name") or ""
            if name:
                failed.append(str(name))
    return failed


def fetch_gerrit_data(
    client: GerritClient,
    web_base: str,
    commits: list[tuple[str, str, str, str | None]],
) -> list[GlogCommit]:
    """Query Gerrit for each commit and return populated GlogCommit objects."""
    result: list[GlogCommit] = []
    ids_in_range = [cid for _, _, _, cid in commits if cid]
    cache = batch_load_change_details(client, ids_in_range)

    needs_comment_count: list[tuple[int, str]] = []
    needs_checks: list[tuple[int, str]] = []

    for sha, short, summary, change_id in commits:
        if not change_id:
            result.append(
                GlogCommit(
                    sha=sha,
                    short_sha=short,
                    summary=summary,
                    change_id=None,
                    pushed=False,
                    patchset_status="absent",
                    verified=None,
                    code_review=None,
                    comments_unresolved=0,
                )
            )
            continue

        detail = cache.get(norm_change_id(change_id))
        if detail is None:
            detail = query_single_change(client, change_id)
            if detail:
                cid = detail.get("change_id")
                if isinstance(cid, str):
                    cache[norm_change_id(cid)] = detail

        if not detail:
            result.append(
                GlogCommit(
                    sha=sha,
                    short_sha=short,
                    summary=summary,
                    change_id=change_id,
                    pushed=False,
                    patchset_status="absent",
                    verified=None,
                    code_review=None,
                    comments_unresolved=0,
                )
            )
            continue

        labels = detail.get("labels") or {}
        verified = extract_label_value(labels, "Verified")
        code_review = extract_label_value(labels, "Code-Review")
        submittable = bool(detail.get("submittable"))
        url = gerrit_change_url(web_base, detail)
        api_id = str(detail.get("id") or change_id)

        raw_u = detail.get("unresolved_comment_count")
        if isinstance(raw_u, int):
            unresolved = raw_u
        else:
            unresolved = 0
            needs_comment_count.append((len(result), api_id))

        if verified is not None and verified < 0:
            needs_checks.append((len(result), api_id))

        ps = patchset_status(sha, detail)
        result.append(
            GlogCommit(
                sha=sha,
                short_sha=short,
                summary=summary,
                change_id=change_id,
                pushed=True,
                patchset_status=ps,
                verified=verified,
                code_review=code_review,
                comments_unresolved=unresolved,
                ci_failures=[],
                gerrit_url=url,
                submittable=submittable,
            )
        )

    workers = min(_PARALLEL_IO, max(len(needs_comment_count), len(needs_checks), 1))

    if needs_comment_count:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            future_to_idx = {
                ex.submit(count_unresolved_via_comments, client, aid): idx for idx, aid in needs_comment_count
            }
            for fut in as_completed(future_to_idx):
                idx = future_to_idx[fut]
                try:
                    result[idx].comments_unresolved = fut.result()
                except Exception as e:
                    logger.debug("unresolved comment count failed: %s", e)

    if needs_checks:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            future_to_idx = {ex.submit(fetch_check_failures, client, aid): idx for idx, aid in needs_checks}
            for fut in as_completed(future_to_idx):
                idx = future_to_idx[fut]
                try:
                    result[idx].ci_failures = fut.result()
                except Exception as e:
                    logger.debug("checks API failed: %s", e)

    return result


def determine_attention(commit: GlogCommit, *, chain_blocked: bool) -> list[str]:
    """Return reasons why this commit needs attention (empty = stable)."""
    reasons: list[str] = []
    if commit.patchset_status == "absent":
        reasons.append("not-pushed")
        return reasons
    if commit.patchset_status == "newer":
        reasons.append("ahead-of-gerrit")
    if commit.patchset_status == "outdated":
        reasons.append("outdated-patchset")
    if commit.verified == -1:
        reasons.append("ci-failed")
    if commit.code_review is not None and commit.code_review < 0:
        reasons.append("review-issues")
    if commit.code_review != 2:
        reasons.append("awaiting-review")
    if commit.comments_unresolved > 0:
        reasons.append("unresolved-comments")
    if chain_blocked:
        reasons.append("chain-blocked")
    return reasons
