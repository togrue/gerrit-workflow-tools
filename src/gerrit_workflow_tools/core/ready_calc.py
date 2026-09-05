"""Compute stack push boundaries from stop-pattern rules or project strategies."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from gerrit_workflow_tools.core.change_id import ChangeIdRow, first_change_id_boundary
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.git_run import GitError, git_out
from gerrit_workflow_tools.core.ready_strategy import (
    BoundaryResult,
    ReadyCommitRow,
    find_ready_boundary_via_registry,
)
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


def change_id_boundary_for_commits(commits: list[ReadyCommitRow]) -> BoundaryResult:
    """Ready boundary at the first missing/malformed/duplicate Change-Id, if any."""
    rows = [ChangeIdRow(sha=c.sha, short_sha=c.short_sha, change_id=c.change_id) for c in commits]
    block_index, reason = first_change_id_boundary(rows, strict=True)
    return BoundaryResult(block_index=block_index, reason=reason)


def earlier_boundary(left: BoundaryResult, right: BoundaryResult) -> BoundaryResult:
    """Return the boundary with the smaller ``block_index`` (earliest wins; tie keeps *left*)."""
    if left.block_index is None and right.block_index is None:
        return left
    if left.block_index is None:
        return right
    if right.block_index is None:
        return left
    if right.block_index < left.block_index:
        return right
    return left


def compose_ready_boundary(strategy: BoundaryResult, commits: list[ReadyCommitRow]) -> BoundaryResult:
    """Tighten a strategy/stop boundary with the first Change-Id ERROR on *commits*."""
    return earlier_boundary(strategy, change_id_boundary_for_commits(commits))


def _ready_result_from_block(
    *,
    shas: list[str],
    target_tip: str,
    block_idx: int | None,
    boundary_reason: str,
    until_sha: str | None,
    until: str | None,
) -> ReadyResult:
    """Build :class:`ReadyResult` from a composed block index (and optional ``--until``)."""
    if block_idx is not None and (block_idx < 0 or block_idx >= len(shas)):
        block_idx = None

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
    """Compute how many commits are safe to push before a ready boundary (or entire stack with ``--all``).

    Change-Id errors always tighten the tip (including with ``--all``). Stop pattern / ready
    strategies are ignored when ``all_commits`` is true.
    """
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
        strategy = BoundaryResult(block_index=None, reason="ignored (--all)")
    else:
        strategy = find_ready_boundary_via_registry(
            cwd,
            project=project,
            commits=ready_rows,
            stop_pattern=stop_pattern,
            overlay=overlay,
            settings=settings,
            web_base=web_base,
        )

    boundary = compose_ready_boundary(strategy, ready_rows)
    block_idx = boundary.block_index
    boundary_reason = boundary.reason
    logger.debug("compute_ready block_idx=%s reason=%s", block_idx, boundary_reason)

    return _ready_result_from_block(
        shas=shas,
        target_tip=target_tip,
        block_idx=block_idx,
        boundary_reason=boundary_reason,
        until_sha=until_sha,
        until=until,
    )
