from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from gerrit_workflow_tools.config import stop_patterns
from gerrit_workflow_tools.git_run import GitError, git_out
from gerrit_workflow_tools.stack import (
    merge_base_with_target,
    parse_change_id,
    stack_commits_metadata_one_log,
    stack_shas_and_subjects_one_log,
)

logger = logging.getLogger(__name__)


@dataclass
class ReadyResult:
    pushable_count: int
    boundary_sha: str | None
    boundary_reason: str
    merge_base: str
    push_tip_sha: str | None
    push_range: str | None  # "mb..tip"


def _filter_patterns(
    patterns: list[str],
    *,
    no_config: bool,
    ignore_exact: list[str],
) -> list[str]:
    if no_config:
        return []
    out = list(patterns)
    for ign in ignore_exact:
        out = [p for p in out if p != ign]
    return out


def _first_block_index(subjects: list[str], patterns: list[str]) -> tuple[int | None, str | None]:
    import re

    for i, sub in enumerate(subjects):
        for pat in patterns:
            try:
                if re.search(pat, sub):
                    return i, pat
            except re.error:
                continue
    return None, None


def compute_ready(
    cwd: Path | str | None,
    *,
    branch: str | None = None,
    all_commits: bool = False,
    no_config_patterns: bool = False,
    ignore_patterns: list[str] | None = None,
    until: str | None = None,
) -> ReadyResult:
    """Compute how many commits are safe to push before a stop-pattern boundary (or entire stack with ``--all``)."""
    mb, _target, _ = merge_base_with_target(cwd, branch)
    raw_patterns = stop_patterns(cwd)
    patterns = _filter_patterns(
        raw_patterns,
        no_config=no_config_patterns,
        ignore_exact=list(ignore_patterns or []),
    )
    shas, subjects = stack_shas_and_subjects_one_log(cwd, mb, branch=branch)
    logger.debug(
        "compute_ready merge_base=%s commits=%d all_commits=%s stop_patterns=%d",
        mb[:8],
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
            merge_base=mb,
            push_tip_sha=tip,
            push_range=f"{mb}..{tip}" if tip else None,
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
            merge_base=mb,
            push_tip_sha=tip,
            push_range=f"{mb}..{tip}" if tip else None,
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
            merge_base=mb,
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
        merge_base=mb,
        push_tip_sha=tip,
        push_range=f"{mb}..{tip}",
    )


def change_id_rows_for_range(
    cwd: Path | str | None,
    merge_base: str,
    *,
    head: str = "HEAD",
) -> list[tuple[str, str, str | None]]:
    """Return ``(full_sha, short_sha, change_id)`` for each commit in ``merge_base..head``."""
    meta = stack_commits_metadata_one_log(cwd, f"{merge_base}..{head}")
    return [(sha, short, parse_change_id(raw)) for sha, short, _sub, raw in meta]


def change_id_rows_for_rev_range(
    cwd: Path | str | None,
    start_exclusive: str,
    end_inclusive: str,
) -> list[tuple[str, str, str | None]]:
    """Return ``(full_sha, short_sha, change_id)`` for ``start_exclusive..end_inclusive``."""
    meta = stack_commits_metadata_one_log(cwd, f"{start_exclusive}..{end_inclusive}")
    return [(sha, short, parse_change_id(raw)) for sha, short, _sub, raw in meta]
