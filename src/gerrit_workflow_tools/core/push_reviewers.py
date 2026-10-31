"""Core reviewer strategy operations for Gerrit push flows."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from gerrit_workflow_tools.core.changeish import parse
from gerrit_workflow_tools.core.config import ConfigError, Settings
from gerrit_workflow_tools.core.gerrit.rest import GerritApiError, GerritRest, resolve_gerrit_web_base
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.gerrit.stack_context import build_triplet, resolve_stack_context
from gerrit_workflow_tools.core.ready_calc import ReadyResult
from gerrit_workflow_tools.core.reviewer import (
    ReviewerStrategy,
    gerrit_credentials_configured,
    reviewer_accounts_from_change_info,
)
from gerrit_workflow_tools.core.stack import Commit, commits_in_range


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


@dataclass(frozen=True)
class PushMark:
    """Current Gerrit reviewer and WIP marks for one commit."""

    has_remote: bool
    reviewers: tuple[str, ...]
    wip: bool
    private: bool


def needs_rest_assignment(strategy: ReviewerStrategy, reviewers: Sequence[str]) -> bool:
    """True when lazy or overwrite must assign reviewers after the push."""
    return strategy in (ReviewerStrategy.LAZY, ReviewerStrategy.OVERWRITE) and bool(reviewers)


def credential_error(strategy: ReviewerStrategy, reviewers: Sequence[str], settings: Settings) -> str | None:
    """Why lazy/overwrite cannot call REST, or ``None`` when they can or need not."""
    if not needs_rest_assignment(strategy, reviewers):
        return None
    if not gerrit_credentials_configured(settings):
        return (
            "error: lazy/overwrite reviewer strategies need Gerrit REST; set gerrit.user and "
            "gerrit.token (or gerrit.password)"
        )
    try:
        resolve_gerrit_web_base(settings)
    except ValueError as exc:
        return f"error: {exc}"
    return None


def push_marks(
    cwd: Path,
    rows: Sequence[Commit],
    *,
    settings: Settings,
    gerrit: GerritRest | None,
) -> dict[str, PushMark]:
    stack = resolve_stack_context(cwd, settings=settings)
    triplets = {
        row.change_id: build_triplet(stack.project, stack.push_branch, row.change_id) for row in rows if row.change_id
    }
    if not triplets:
        return {}
    resolve_gerrit_web_base(settings)
    if not gerrit_credentials_configured(settings):
        raise ConfigError(
            "Gerrit credentials are not configured; set gerrit.user and "
            "gerrit.token (or gerrit.password) for REST access."
        )
    raw = GerritService.from_cwd(cwd, settings=settings, rest=gerrit).changes.get_payloads(list(triplets.values()))
    by_triplet = _payloads_by_triplet(raw)
    marks: dict[str, PushMark] = {}
    for change_id, triplet in triplets.items():
        detail = by_triplet.get(triplet)
        if detail is None:
            marks[change_id] = PushMark(False, (), False, False)
            continue
        marks[change_id] = PushMark(
            True,
            tuple(account.slug for account in reviewer_accounts_from_change_info(detail)),
            bool(detail.get("work_in_progress")),
            bool(detail.get("private")),
        )
    return marks


def change_refs_for_commits(cwd: Path, commits: Sequence[Commit], *, settings: Settings) -> list[str]:
    stack = resolve_stack_context(cwd, settings=settings)
    out: list[str] = []
    seen: set[str] = set()
    for commit in commits:
        if not commit.change_id:
            continue
        triplet = build_triplet(stack.project, stack.push_branch, commit.change_id)
        if triplet not in seen:
            seen.add(triplet)
            out.append(triplet)
    return out


def stack_change_refs_ordered(cwd: Path, ready: ReadyResult, first_parent: bool, *, settings: Settings) -> list[str]:
    """Unique Gerrit triplets in stack order for the current push range."""
    if not ready.push_range:
        return []
    rows = commits_in_range(cwd, ready.push_range, first_parent=first_parent)
    return change_refs_for_commits(cwd, rows, settings=settings)


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

        footer_change_id = parse(triplet).change_id
        if footer_change_id is None:
            issues.append(ReviewerApplyIssue(level="error", message=f"not a Gerrit triplet: {triplet}"))
            return ReviewerApplyResult(ok=False, issues=issues)
        existing = reviewer_accounts_from_change_info(detail)
        if strategy == ReviewerStrategy.LAZY and existing:
            outcomes.append(ReviewerApplyChangeOutcome(change_id=footer_change_id, reviewers_assigned=()))
            continue

        remove: list[int] = []
        if strategy == ReviewerStrategy.OVERWRITE:
            reviewers_only = reviewer_accounts_from_change_info(detail, roles=("REVIEWER",))
            remove = [account.account_id for account in reviewers_only if account.account_id is not None]
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
        outcomes.append(ReviewerApplyChangeOutcome(change_id=footer_change_id, reviewers_assigned=tuple(reviewers)))

    return ReviewerApplyResult(ok=True, issues=issues, outcomes=outcomes)
