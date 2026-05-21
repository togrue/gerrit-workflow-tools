"""Compute stack push boundaries from stop-pattern rules."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from gerrit_workflow_tools.core.change_id import ChangeIdRow
from gerrit_workflow_tools.core.config import stop_patterns
from gerrit_workflow_tools.core.git_run import GitError, git_out
from gerrit_workflow_tools.core.stack import commits_in_range, merge_base_with_target

logger = logging.getLogger(__name__)


@dataclass
class ReadyResult:
    """Boundary calculation output describing what portion of a stack is pushable."""

    pushable_count: int
    boundary_sha: str | None
    boundary_reason: str
    target_tip: str
    push_tip_sha: str | None
    push_range: str | None  # "upstream_tip..tip" (target_tip field holds upstream tip SHA)


def _filter_patterns(patterns: list[str], *, ignore_exact: list[str]) -> list[str]:
    out = list(patterns)
    for ign in ignore_exact:
        out = [p for p in out if p != ign]
    return out


def _first_block_index(subjects: list[str], patterns: list[str]) -> tuple[int | None, str | None]:
    for i, sub in enumerate(subjects):
        for pat in patterns:
            try:
                if re.search(pat, sub, re.IGNORECASE):
                    return i, pat
            except re.error:
                continue
    return None, None


# pylint: disable=too-many-locals
def compute_ready(
    cwd: Path | str | None,
    *,
    branch: str | None = None,
    head: str = "HEAD",
    all_commits: bool = False,
    ignore_patterns: list[str] | None = None,
    until: str | None = None,
    first_parent: bool = True,
) -> ReadyResult:
    """Compute how many commits are safe to push before a stop-pattern boundary (or entire stack with ``--all``)."""
    _fork, _display, target_tip = merge_base_with_target(cwd, branch, head=head)
    raw_patterns = stop_patterns(cwd)
    patterns = _filter_patterns(raw_patterns, ignore_exact=list(ignore_patterns or []))
    rows = commits_in_range(cwd, f"{target_tip}..{head}", first_parent=first_parent)
    shas = [r.sha for r in rows]
    subjects = [r.subject for r in rows]
    logger.debug(
        "compute_ready target_tip=%s commits=%d all_commits=%s stop_patterns=%d",
        target_tip[:8],
        len(shas),
        all_commits,
        len(patterns),
    )

    until_sha: str | None = None
    if until:
        until_sha = git_out("rev-parse", until.strip(), cwd=cwd)
        if until_sha not in shas:
            raise GitError(f"commit {until} is not in the current stack")

    if all_commits:
        tip_idx = len(shas) - 1 if shas else -1
        if until_sha:
            tip_idx = shas.index(until_sha)
        tip = shas[tip_idx] if tip_idx >= 0 else None
        return ReadyResult(
            pushable_count=len(shas) if until_sha is None else tip_idx + 1,
            boundary_sha=None,
            boundary_reason="ignored (--all)",
            target_tip=target_tip,
            push_tip_sha=tip,
            push_range=f"{target_tip}..{tip}" if tip else None,
        )

    block_idx, matched_pat = _first_block_index(subjects, patterns)
    logger.debug(
        "compute_ready block_idx=%s matched_pat=%s",
        block_idx,
        matched_pat,
    )

    if block_idx is None:
        # all ready
        tip_idx = len(shas) - 1 if shas else -1
        if until_sha:
            tip_idx = shas.index(until_sha)
        tip = shas[tip_idx] if tip_idx >= 0 else None
        n = tip_idx + 1 if tip_idx >= 0 else 0
        return ReadyResult(
            pushable_count=n,
            boundary_sha=None,
            boundary_reason="no stop pattern matched",
            target_tip=target_tip,
            push_tip_sha=tip,
            push_range=f"{target_tip}..{tip}" if tip else None,
        )

    # Pushable: commits before block_idx
    pushable_count = block_idx
    boundary_sha = shas[block_idx]
    boundary_reason = f"subject matches stop pattern {matched_pat!r}"

    if pushable_count == 0:
        return ReadyResult(
            pushable_count=0,
            boundary_sha=boundary_sha,
            boundary_reason=boundary_reason,
            target_tip=target_tip,
            push_tip_sha=None,
            push_range=None,
        )

    tip_idx = pushable_count - 1
    tip = shas[tip_idx]
    if until_sha:
        uidx = shas.index(until_sha)
        if uidx >= block_idx:
            raise GitError(
                f"revision {until!r} is at or after the ready boundary; choose a commit before the blocking commit."
            )
        tip_idx = uidx
        tip = until_sha
        pushable_count = tip_idx + 1

    return ReadyResult(
        pushable_count=pushable_count,
        boundary_sha=boundary_sha,
        boundary_reason=boundary_reason,
        target_tip=target_tip,
        push_tip_sha=tip,
        push_range=f"{target_tip}..{tip}",
    )


def change_id_rows_for_range(
    cwd: Path | str | None,
    start_exclusive: str,
    *,
    head: str = "HEAD",
    first_parent: bool = True,
) -> list[ChangeIdRow]:
    """Return named rows for each commit in ``start_exclusive..head``."""
    meta = commits_in_range(cwd, f"{start_exclusive}..{head}", first_parent=first_parent)
    return [ChangeIdRow(sha=c.sha, short_sha=c.short_sha, change_id=c.change_id) for c in meta]
