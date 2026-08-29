"""Compute stack push boundaries from stop-pattern rules or project strategies."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from gerrit_workflow_tools.core.change_id import ChangeIdRow
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.git_run import GitError, git_out
from gerrit_workflow_tools.core.ready_strategy import ReadyCommitRow, find_ready_boundary_via_registry
from gerrit_workflow_tools.core.stack import commits_in_range, merge_base_with_target


if TYPE_CHECKING:
    from gerrit_workflow_tools.core.gerrit_change_status import LogCommit

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


# pylint: disable=too-many-locals
def compute_ready(
    cwd: Path | str | None,
    *,
    branch: str | None = None,
    head: str = "HEAD",
    all_commits: bool = False,
    until: str | None = None,
    first_parent: bool = True,
    stop_pattern: str,
    project: str = "",
    settings: Settings | None = None,
    web_base: str | None = None,
    overlay: dict[str, LogCommit] | None = None,
) -> ReadyResult:
    """Compute how many commits are safe to push before a ready boundary (or entire stack with ``--all``)."""
    _fork, _display, target_tip = merge_base_with_target(cwd, branch, head=head)
    rows = commits_in_range(cwd, f"{target_tip}..{head}", first_parent=first_parent)
    shas = [r.sha for r in rows]
    ready_rows = [
        ReadyCommitRow(sha=r.sha, short_sha=r.short_sha, subject=r.subject, change_id=r.change_id) for r in rows
    ]
    logger.debug(
        "compute_ready target_tip=%s commits=%d all_commits=%s stop_pattern=%r project=%r",
        target_tip[:8],
        len(shas),
        all_commits,
        stop_pattern,
        project,
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

    boundary = find_ready_boundary_via_registry(
        cwd,
        project=project,
        commits=ready_rows,
        stop_pattern=stop_pattern,
        overlay=overlay,
        settings=settings,
        web_base=web_base,
    )
    block_idx = boundary.block_index
    boundary_reason = boundary.reason
    logger.debug("compute_ready block_idx=%s reason=%s", block_idx, boundary_reason)

    if block_idx is None:
        tip_idx = len(shas) - 1 if shas else -1
        if until_sha:
            tip_idx = shas.index(until_sha)
        tip = shas[tip_idx] if tip_idx >= 0 else None
        n = tip_idx + 1 if tip_idx >= 0 else 0
        return ReadyResult(
            pushable_count=n,
            boundary_sha=None,
            boundary_reason=boundary_reason,
            target_tip=target_tip,
            push_tip_sha=tip,
            push_range=f"{target_tip}..{tip}" if tip else None,
        )

    if block_idx < 0 or block_idx >= len(shas):
        tip_idx = len(shas) - 1 if shas else -1
        tip = shas[tip_idx] if tip_idx >= 0 else None
        return ReadyResult(
            pushable_count=len(shas),
            boundary_sha=None,
            boundary_reason=boundary_reason,
            target_tip=target_tip,
            push_tip_sha=tip,
            push_range=f"{target_tip}..{tip}" if tip else None,
        )

    pushable_count = block_idx
    boundary_sha = shas[block_idx]

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
