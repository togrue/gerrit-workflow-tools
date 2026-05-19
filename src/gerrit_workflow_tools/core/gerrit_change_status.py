"""Shared Gerrit change status for local commits (used by ``ger log`` and ``ger show``)."""

from __future__ import annotations

import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import quote

from gerrit_workflow_tools.core.gerrit_client import GerritApiError, GerritClient
from gerrit_workflow_tools.core.git_run import git

logger = logging.getLogger(__name__)

# Batched change query: labels + submittable + revisions in one round trip (no separate /detail).
LOG_QUERY_OPTIONS = (
    "DETAILED_LABELS",
    "SUBMITTABLE",
    "CURRENT_REVISION",
    "ALL_REVISIONS",
)
_BATCH_OR_CHUNK = 25
_PARALLEL_IO = 8


class PatchsetStatus(str, Enum):
    """Bounded Gerrit-vs-local patchset states for one commit."""

    ACTIVE = "active"
    NEWER = "newer"
    OUTDATED = "outdated"
    ABSENT = "absent"
    MERGED_SAME = "merged-same"
    MERGED_DRIFT = "merged-drift"
    MERGED_UNKNOWN = "merged-unknown"


@dataclass(frozen=True)
class CommitStatusInput:
    """Input row for Gerrit status enrichment."""

    sha: str
    short_sha: str
    summary: str
    change_id: str | None


@dataclass(frozen=True)
class ReviewerAccount:
    """Normalized Gerrit reviewer account identity used by CLI and core."""

    slug: str
    account_id: int | None = None


@dataclass(frozen=True)
class InlineComment:
    """Normalized unresolved inline comment payload."""

    path: str
    line: int | None
    message: str
    comment_id: str | None = None


@dataclass
class LogCommit:  # pylint: disable=too-many-instance-attributes
    """Aggregated local+Gerrit status for a commit shown by CLI status commands."""

    sha: str
    short_sha: str
    summary: str
    change_id: str | None
    pushed: bool  # True if a Gerrit change exists for this Change-Id (any patchset state)
    abandoned: bool  # True when Gerrit change status is ABANDONED
    patchset_status: PatchsetStatus
    verified: int | None  # -1, 0, +1; None = no vote
    code_review: int | None  # -2, -1, 0, +1, +2; None = no vote
    comments_unresolved: int
    ci_failures: list[str] = field(default_factory=list)
    gerrit_url: str | None = None
    submittable: bool = False
    attention_reasons: list[str] = field(default_factory=list)
    change_status: str | None = None  # raw Gerrit ``status`` (e.g. NEW, MERGED)
    merged_equivalent: bool | None = None  # MERGED only: proved same / different / unknown
    reviewers: list[ReviewerAccount] = field(default_factory=list)


def commit_blocks_chain_for_submittability(commit: LogCommit) -> bool:
    """Return whether this commit should block later commits in a dependency chain.

    Default rule: a pushed change that is not submittable on Gerrit blocks followers.

    **MERGED** changes use local equivalence semantics (v1):

    * ``merged-same`` — does **not** block (already integrated upstream).
    * ``merged-drift`` — **blocks** (local patch provably differs from merged revision).
    * ``merged-unknown`` — **blocks** (cannot prove equivalence; treat conservatively).

    Open (non-MERGED) changes keep the usual ``not submittable`` rule.
    """
    if commit.patchset_status == PatchsetStatus.MERGED_SAME:
        return False
    if commit.patchset_status in (PatchsetStatus.MERGED_DRIFT, PatchsetStatus.MERGED_UNKNOWN):
        return True
    if not commit.pushed:
        return False
    return not commit.submittable


def _is_merge_commit(cwd: Path | str | None, sha: str) -> bool:
    """True if *sha* has a second parent (merge commit)."""
    p = git("rev-parse", "--verify", f"{sha}^2", cwd=cwd, check=False)
    return p.returncode == 0


def _patch_id_single_parent(cwd: Path | str | None, sha: str) -> str | None:
    """Return ``git patch-id`` first token for the single-commit diff against its first parent."""
    p = git("rev-parse", "--verify", f"{sha}^", cwd=cwd, check=False)
    if p.returncode != 0:
        return None
    parent = p.stdout.strip()
    diff_p = git("diff", f"{parent}..{sha}", cwd=cwd, check=False)
    if diff_p.returncode != 0 or not diff_p.stdout.strip():
        return None
    pid = subprocess.run(
        ["git", "patch-id"],
        cwd=str(cwd) if cwd is not None else None,
        input=diff_p.stdout,
        text=True,
        capture_output=True,
        check=False,
    )
    if pid.returncode != 0 or not pid.stdout.strip():
        return None
    line = pid.stdout.strip().splitlines()[0]
    parts = line.split()
    return parts[0] if parts else None


def compute_merged_equivalent(
    local_sha: str,
    detail: dict[str, Any],
    cwd: Path | str | None,
) -> bool | None:
    """
    v1 conservative equivalence: SHA match, else patch-id match when both sides are single-parent
    and the Gerrit ``current_revision`` exists locally. Merge commits and missing objects → ``None``.
    """
    cur = detail.get("current_revision")
    if not isinstance(cur, str) or not cur.strip():
        return None
    if norm_sha(local_sha) == norm_sha(cur):
        return True
    p = git("rev-parse", "-q", "--verify", f"{cur}^{{commit}}", cwd=cwd, check=False)
    if p.returncode != 0:
        return None
    if _is_merge_commit(cwd, local_sha) or _is_merge_commit(cwd, cur):
        return None
    pl = _patch_id_single_parent(cwd, local_sha)
    pg = _patch_id_single_parent(cwd, cur)
    if pl is None or pg is None:
        return None
    return pl == pg


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
    """Count unresolved inline comments across Gerrit file->comments mappings."""

    count = 0
    for comments in file_map.values():
        for c in comments:
            if isinstance(c, dict) and c.get("unresolved") is True:
                count += 1
    return count


def _comment_line(c: dict[str, Any]) -> int | None:
    ln = c.get("line")
    if isinstance(ln, int):
        return ln
    rng = c.get("range")
    if isinstance(rng, dict):
        start_line = rng.get("start_line")
        if isinstance(start_line, int):
            return start_line
    return None


def collect_unresolved_comments(file_map: dict[str, list[dict[str, Any]]]) -> list[InlineComment]:
    """Normalize unresolved comments from Gerrit file map, sorted by path/line."""

    rows: list[InlineComment] = []
    for path, comments in file_map.items():
        for comment in comments:
            if not isinstance(comment, dict) or comment.get("unresolved") is not True:
                continue
            raw_msg = comment.get("message")
            raw_id = comment.get("id")
            rows.append(
                InlineComment(
                    path=path,
                    line=_comment_line(comment),
                    message=raw_msg if isinstance(raw_msg, str) else "",
                    comment_id=raw_id if isinstance(raw_id, str) else None,
                )
            )
    rows.sort(key=lambda r: (r.path, r.line if r.line is not None else -1))
    return rows


def norm_change_id(change_id: str) -> str:
    """Normalize Change-Id values for case-insensitive lookups."""

    return change_id.lower()


def norm_sha(sha: str) -> str:
    """Normalize commit SHA text for case-insensitive comparisons."""

    return sha.strip().lower()


def patchset_status(local_sha: str, detail: dict[str, Any]) -> PatchsetStatus:
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
        return PatchsetStatus.ACTIVE
    if rev_keys and ls in rev_keys and cur_n and ls != cur_n:
        return PatchsetStatus.OUTDATED
    if cur_n or rev_keys:
        return PatchsetStatus.NEWER
    return PatchsetStatus.NEWER


def _load_reviewers_for_change(client: GerritClient, api_change_id: str) -> list[ReviewerAccount]:
    """Load reviewers from change detail (batched queries omit the ``reviewers`` field)."""
    from gerrit_workflow_tools.core.reviewer import reviewer_accounts_from_change_info

    try:
        detail = client.get_change(api_change_id)
    except GerritApiError as e:
        logger.debug("reviewer detail fetch failed for %s: %s", api_change_id, e)
        return []
    return reviewer_accounts_from_change_info(detail)


def count_unresolved_via_comments(client: GerritClient, api_change_id: str) -> int:
    """Fetch comments for one change and return unresolved count, logging and defaulting to 0 on errors."""

    try:
        file_map = client.get_comments(api_change_id)
        return count_unresolved_in_file_map(file_map)
    except GerritApiError as e:
        logger.warning("Gerrit comments failed for %s: %s", api_change_id, e)
        return 0


def _ingest_change_rows(out: dict[str, dict[str, Any]], rows: list[Any]) -> None:
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_id = row.get("change_id")
        if isinstance(raw_id, str):
            out[norm_change_id(raw_id)] = row


def _fallback_query_chunk(client: GerritClient, chunk: list[str]) -> list[dict[str, Any]]:
    """Query each Change-Id in *chunk* when a batched OR query fails."""
    rows: list[dict[str, Any]] = []
    workers = min(_PARALLEL_IO, len(chunk))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for one in ex.map(lambda c: query_single_change(client, c), chunk):
            if one:
                rows.append(one)
    return rows


def _query_change_chunk(client: GerritClient, chunk: list[str], opts: list[str]) -> list[dict[str, Any]]:
    q = " OR ".join(f"change:{c}" for c in chunk)
    try:
        return client.query_changes(q, n=len(chunk) + 10, options=opts)
    except GerritApiError as e:
        logger.warning("batched Gerrit query failed (%s), falling back per change", e)
        return _fallback_query_chunk(client, chunk)


def _parallel_fill_cache_misses(
    client: GerritClient,
    cache: dict[str, dict[str, Any]],
    change_ids: list[str],
) -> None:
    missing = [cid for cid in change_ids if norm_change_id(cid) not in cache]
    if not missing:
        return
    workers = min(_PARALLEL_IO, len(missing))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for detail in ex.map(lambda c: query_single_change(client, c), missing):
            if not detail:
                continue
            raw_id = detail.get("change_id")
            if isinstance(raw_id, str):
                cache[norm_change_id(raw_id)] = detail


def batch_load_change_details(client: GerritClient, change_ids: list[str]) -> dict[str, dict[str, Any]]:  # pylint: disable=too-many-locals
    """Map normalized Change-Id to ChangeInfo using chunked ``change:I1 OR change:I2`` queries."""
    out: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    unique: list[str] = []
    for cid in change_ids:
        k = norm_change_id(cid)
        if k not in seen:
            seen.add(k)
            unique.append(cid)

    opts = list(LOG_QUERY_OPTIONS)
    chunks = [unique[i : i + _BATCH_OR_CHUNK] for i in range(0, len(unique), _BATCH_OR_CHUNK)]
    if len(chunks) <= 1:
        for chunk in chunks:
            _ingest_change_rows(out, _query_change_chunk(client, chunk, opts))
        return out

    workers = min(_PARALLEL_IO, len(chunks))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for rows in ex.map(lambda ch: _query_change_chunk(client, ch, opts), chunks):
            _ingest_change_rows(out, rows)
    return out


def query_single_change(client: GerritClient, change_id: str) -> dict[str, Any] | None:
    """Query one Gerrit change by Change-Id and return first matching ``ChangeInfo`` row."""

    try:
        rows = client.query_changes(f"change:{change_id}", n=5, options=list(LOG_QUERY_OPTIONS))
    except GerritApiError as e:
        logger.warning("Gerrit query failed for %s: %s", change_id, e)
        return None
    if not rows:
        return None
    return rows[0]


def gerrit_change_url(web_base: str, change: dict[str, Any]) -> str | None:
    """Build the Gerrit change page URL from a ``ChangeInfo`` object."""

    proj = change.get("project")
    num = change.get("_number")
    if not proj or not isinstance(num, int):
        return None
    proj_enc = quote(str(proj), safe="")
    return f"{web_base}/c/{proj_enc}/+/{num}"


def gerrit_inline_comment_url(change_page_url: str | None, comment_id: str | None) -> str | None:
    """Build a PolyGerrit URL that opens the diff view focused on one inline comment.

    Matches the ``/c/<project>/+/<changeNum>/comment/<commentId>/`` route (see Gerrit
    ``gr-router`` COMMENT pattern). *comment_id* is the REST ``CommentInfo.id`` (URL-encoded
    UUID string).
    """
    if not change_page_url or not comment_id or not str(comment_id).strip():
        return None
    base = change_page_url.rstrip("/")
    cid_enc = quote(str(comment_id), safe="")
    return f"{base}/comment/{cid_enc}/"


def fetch_check_failures(client: GerritClient, change_id: str) -> list[str]:
    """Attempt to retrieve failed CI check names via the Gerrit Checks API."""
    enc = quote(change_id, safe="")
    try:
        data = client.get_json(f"changes/{enc}/revisions/current/checks")
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


# pylint: disable=too-many-branches,too-many-locals,too-many-statements
def fetch_gerrit_data(
    client: GerritClient,
    web_base: str,
    commits: list[CommitStatusInput],
    *,
    cwd: Path | str | None = None,
) -> list[LogCommit]:
    """Query Gerrit for each commit and return populated LogCommit objects."""

    from gerrit_workflow_tools.core.reviewer import reviewer_accounts_from_change_info

    resolved_cwd = Path.cwd() if cwd is None else Path(cwd)
    result: list[LogCommit] = []
    ids_in_range = [c.change_id for c in commits if c.change_id]
    cache = batch_load_change_details(client, ids_in_range)
    _parallel_fill_cache_misses(client, cache, ids_in_range)

    follow_ups: list[tuple[str, int, str]] = []

    for row in commits:
        sha, short, summary, change_id = row.sha, row.short_sha, row.summary, row.change_id
        if not change_id:
            result.append(
                LogCommit(
                    sha=sha,
                    short_sha=short,
                    summary=summary,
                    change_id=None,
                    pushed=False,
                    abandoned=False,
                    patchset_status=PatchsetStatus.ABSENT,
                    verified=None,
                    code_review=None,
                    comments_unresolved=0,
                    change_status=None,
                    merged_equivalent=None,
                )
            )
            continue

        detail = cache.get(norm_change_id(change_id))

        if not detail:
            result.append(
                LogCommit(
                    sha=sha,
                    short_sha=short,
                    summary=summary,
                    change_id=change_id,
                    pushed=False,
                    abandoned=False,
                    patchset_status=PatchsetStatus.ABSENT,
                    verified=None,
                    code_review=None,
                    comments_unresolved=0,
                    change_status=None,
                    merged_equivalent=None,
                )
            )
            continue

        labels = detail.get("labels") or {}
        verified = extract_label_value(labels, "Verified")
        code_review = extract_label_value(labels, "Code-Review")
        submittable = bool(detail.get("submittable"))
        raw_status = detail.get("status")
        st = raw_status.upper() if isinstance(raw_status, str) else ""
        change_status_val = raw_status if isinstance(raw_status, str) else None
        abandoned = st == "ABANDONED"
        url = gerrit_change_url(web_base, detail)
        api_id = str(detail.get("id") or change_id)

        raw_u = detail.get("unresolved_comment_count")
        if isinstance(raw_u, int):
            unresolved = raw_u
        else:
            unresolved = 0
            follow_ups.append(("comments", len(result), api_id))

        if verified is not None and verified < 0:
            follow_ups.append(("checks", len(result), api_id))

        # List/batch ChangeInfo may include an empty ``reviewers`` stub; only skip the
        # detail fetch when the batch row already has parsed reviewer accounts.
        reviewer_list = reviewer_accounts_from_change_info(detail)
        if not reviewer_list:
            follow_ups.append(("reviewers", len(result), api_id))

        merged_eq: bool | None = None
        if st == "MERGED":
            merged_eq = compute_merged_equivalent(sha, detail, resolved_cwd)
            if merged_eq is True:
                ps = PatchsetStatus.MERGED_SAME
            elif merged_eq is False:
                ps = PatchsetStatus.MERGED_DRIFT
            else:
                ps = PatchsetStatus.MERGED_UNKNOWN
        else:
            ps = patchset_status(sha, detail)

        result.append(
            LogCommit(
                sha=sha,
                short_sha=short,
                summary=summary,
                change_id=change_id,
                pushed=True,
                abandoned=abandoned,
                patchset_status=ps,
                verified=verified,
                code_review=code_review,
                comments_unresolved=unresolved,
                ci_failures=[],
                gerrit_url=url,
                submittable=submittable,
                change_status=change_status_val,
                merged_equivalent=merged_eq,
                reviewers=reviewer_list,
            )
        )

    if follow_ups:
        workers = min(_PARALLEL_IO, len(follow_ups))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            future_meta: dict[Any, tuple[str, int]] = {}
            for kind, idx, api_id in follow_ups:
                if kind == "comments":
                    future_meta[ex.submit(count_unresolved_via_comments, client, api_id)] = (kind, idx)
                elif kind == "checks":
                    future_meta[ex.submit(fetch_check_failures, client, api_id)] = (kind, idx)
                else:
                    future_meta[ex.submit(_load_reviewers_for_change, client, api_id)] = (kind, idx)
            for fut in as_completed(future_meta):
                kind, idx = future_meta[fut]
                exc = fut.exception()
                if exc is not None:
                    logger.debug("%s follow-up failed: %s", kind, exc)
                    continue
                if kind == "comments":
                    result[idx].comments_unresolved = fut.result()
                elif kind == "checks":
                    result[idx].ci_failures = fut.result()
                else:
                    result[idx].reviewers = fut.result()

    return result


def determine_attention(commit: LogCommit, *, chain_blocked: bool) -> list[str]:  # pylint: disable=too-many-branches
    """Return reasons why this commit needs attention (empty = stable)."""
    reasons: list[str] = []
    if commit.abandoned:
        reasons.append("abandoned")
        return reasons
    if commit.patchset_status == PatchsetStatus.ABSENT:
        reasons.append("not-pushed")
        return reasons
    if commit.patchset_status == PatchsetStatus.MERGED_SAME:
        if chain_blocked:
            reasons.append("chain-blocked")
        return reasons
    if commit.patchset_status == PatchsetStatus.MERGED_DRIFT:
        reasons.append("merged-drift")
        if chain_blocked:
            reasons.append("chain-blocked")
        return reasons
    if commit.patchset_status == PatchsetStatus.MERGED_UNKNOWN:
        reasons.append("merged-unknown")
        if chain_blocked:
            reasons.append("chain-blocked")
        return reasons
    if commit.patchset_status == PatchsetStatus.NEWER:
        reasons.append("ahead-of-gerrit")
    if commit.patchset_status == PatchsetStatus.OUTDATED:
        reasons.append("outdated-patchset")
    if commit.verified == -1:
        reasons.append("ci-failed")
    if len(commit.reviewers) == 0:
        reasons.append("no-reviewers")
    if commit.code_review is not None and commit.code_review < 0:
        reasons.append("review-issues")
    if commit.code_review != 2:
        reasons.append("awaiting-review")
    if commit.comments_unresolved > 0:
        reasons.append("unresolved-comments")
    if chain_blocked:
        reasons.append("chain-blocked")
    return reasons
