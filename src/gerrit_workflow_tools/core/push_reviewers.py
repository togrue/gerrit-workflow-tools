"""Core reviewer strategy operations for Gerrit push flows."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from gerrit_workflow_tools.core.gerrit.rest import change_id_for_gerrit_rest_path
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.gerrit_change_status import norm_change_id
from gerrit_workflow_tools.core.gerrit_client import GerritApiError
from gerrit_workflow_tools.core.ready_calc import ReadyResult
from gerrit_workflow_tools.core.reviewer import ReviewerStrategy, reviewer_accounts_from_change_info
from gerrit_workflow_tools.core.stack import commits_in_range


@dataclass(frozen=True)
class ReviewerApplyIssue:
    """One warning/error produced while applying reviewer strategy."""

    level: str  # "warning" | "error"
    message: str


@dataclass(frozen=True)
class ReviewerApplyChangeOutcome:
    """Per-change result of a lazy/overwrite reviewer pass (``change_id`` is normalized)."""

    change_id: str
    reviewers_assigned: tuple[str, ...]


@dataclass
class ReviewerApplyResult:
    """Result of applying reviewer strategy via Gerrit REST."""

    ok: bool
    issues: list[ReviewerApplyIssue] = field(default_factory=list)
    outcomes: list[ReviewerApplyChangeOutcome] = field(default_factory=list)


def stack_change_ids_ordered(cwd: Path, ready: ReadyResult, first_parent: bool) -> list[str]:
    """Unique normalized Change-Ids in stack order for the current push range."""

    if not ready.push_range:
        return []
    rows = commits_in_range(cwd, ready.push_range, first_parent=first_parent)
    out: list[str] = []
    seen: set[str] = set()
    for commit in rows:
        if not commit.change_id:
            continue
        normalized = norm_change_id(commit.change_id)
        if normalized not in seen:
            seen.add(normalized)
            out.append(normalized)
    return out


def apply_reviewer_strategy_after_push_service(
    service: GerritService,
    strategy: ReviewerStrategy,
    reviewers: list[str],
    change_ids: list[str],
) -> ReviewerApplyResult:
    """Apply lazy/overwrite reviewer strategy through the layered Gerrit service."""

    if strategy == ReviewerStrategy.PUSH or not reviewers:
        return ReviewerApplyResult(ok=True)

    issues: list[ReviewerApplyIssue] = []
    outcomes: list[ReviewerApplyChangeOutcome] = []
    try:
        details_by_id = service.changes.get_payloads(change_ids)
    except GerritApiError as error:
        issues.append(ReviewerApplyIssue(level="error", message=f"could not load changes: {error}"))
        return ReviewerApplyResult(ok=False, issues=issues)

    for change_id in change_ids:
        detail = details_by_id.get(change_id_for_gerrit_rest_path(change_id))
        if detail is None:
            issues.append(ReviewerApplyIssue(level="error", message=f"could not load change {change_id}"))
            return ReviewerApplyResult(ok=False, issues=issues)

        existing = reviewer_accounts_from_change_info(detail)
        if strategy == ReviewerStrategy.LAZY and existing:
            outcomes.append(ReviewerApplyChangeOutcome(change_id=change_id, reviewers_assigned=()))
            continue

        remove: list[int] = []
        if strategy == ReviewerStrategy.OVERWRITE:
            remove = [account.account_id for account in existing if account.account_id is not None]
        try:
            service.changes.set_reviewers(change_id, add=reviewers, remove=remove)
        except GerritApiError as error:
            issues.append(
                ReviewerApplyIssue(
                    level="error",
                    message=f"could not update reviewers on {change_id}: {error}",
                )
            )
            return ReviewerApplyResult(ok=False, issues=issues)
        outcomes.append(ReviewerApplyChangeOutcome(change_id=change_id, reviewers_assigned=tuple(reviewers)))

    return ReviewerApplyResult(ok=True, issues=issues, outcomes=outcomes)
