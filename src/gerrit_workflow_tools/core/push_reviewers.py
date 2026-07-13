"""Core reviewer strategy operations for Gerrit push flows."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from gerrit_workflow_tools.core.gerrit.change_resolution import build_triplet, parse_triplet, resolve_stack_context
from gerrit_workflow_tools.core.gerrit.rest import GerritApiError
from gerrit_workflow_tools.core.gerrit.service import GerritService
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
    """Per-change result of a lazy/overwrite reviewer pass (``change_id`` is the footer Change-Id)."""

    change_id: str
    reviewers_assigned: tuple[str, ...]


@dataclass
class ReviewerApplyResult:
    """Result of applying reviewer strategy via Gerrit REST."""

    ok: bool
    issues: list[ReviewerApplyIssue] = field(default_factory=list)
    outcomes: list[ReviewerApplyChangeOutcome] = field(default_factory=list)


def stack_change_refs_ordered(cwd: Path, ready: ReadyResult, first_parent: bool) -> list[str]:
    """Unique Gerrit triplets in stack order for the current push range."""

    if not ready.push_range:
        return []
    stack = resolve_stack_context(cwd)
    rows = commits_in_range(cwd, ready.push_range, first_parent=first_parent)
    out: list[str] = []
    seen: set[str] = set()
    for commit in rows:
        if not commit.change_id:
            continue
        triplet = build_triplet(stack.project, stack.push_branch, commit.change_id)
        if triplet not in seen:
            seen.add(triplet)
            out.append(triplet)
    return out


def _payloads_by_triplet(payloads: dict[str, dict[str, object]]) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    for ref, payload in payloads.items():
        out[ref] = payload
        triplet = payload.get("id")
        if isinstance(triplet, str) and triplet:
            out[triplet] = payload
    return out


def apply_reviewer_strategy_after_push_service(
    service: GerritService,
    strategy: ReviewerStrategy,
    reviewers: list[str],
    change_refs: list[str],
) -> ReviewerApplyResult:
    """Apply lazy/overwrite reviewer strategy through the layered Gerrit service."""

    if strategy == ReviewerStrategy.PUSH or not reviewers:
        return ReviewerApplyResult(ok=True)

    issues: list[ReviewerApplyIssue] = []
    outcomes: list[ReviewerApplyChangeOutcome] = []
    try:
        raw_payloads = service.changes.get_payloads(change_refs)
    except GerritApiError as error:
        issues.append(ReviewerApplyIssue(level="error", message=f"could not load changes: {error}"))
        return ReviewerApplyResult(ok=False, issues=issues)

    details_by_triplet = _payloads_by_triplet(raw_payloads)

    for triplet in change_refs:
        detail = details_by_triplet.get(triplet)
        if detail is None:
            issues.append(ReviewerApplyIssue(level="error", message=f"could not load change {triplet}"))
            return ReviewerApplyResult(ok=False, issues=issues)

        _, _, footer_change_id = parse_triplet(triplet)
        existing = reviewer_accounts_from_change_info(detail)
        if strategy == ReviewerStrategy.LAZY and existing:
            outcomes.append(ReviewerApplyChangeOutcome(change_id=footer_change_id, reviewers_assigned=()))
            continue

        remove: list[int] = []
        if strategy == ReviewerStrategy.OVERWRITE:
            remove = [account.account_id for account in existing if account.account_id is not None]
        try:
            service.changes.set_reviewers(triplet, add=reviewers, remove=remove)
        except GerritApiError as error:
            issues.append(
                ReviewerApplyIssue(
                    level="error",
                    message=f"could not update reviewers on {triplet}: {error}",
                )
            )
            return ReviewerApplyResult(ok=False, issues=issues)
        outcomes.append(
            ReviewerApplyChangeOutcome(change_id=footer_change_id, reviewers_assigned=tuple(reviewers))
        )

    return ReviewerApplyResult(ok=True, issues=issues, outcomes=outcomes)
