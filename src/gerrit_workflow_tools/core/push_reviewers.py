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
from gerrit_workflow_tools.core.reviewer import (
    ReviewerStrategy,
    gerrit_credentials_configured,
    reviewer_accounts_from_change_info,
)
from gerrit_workflow_tools.core.stack import Commit


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
    service = GerritService.from_cwd(cwd, settings=settings, rest=gerrit)
    by_triplet = service.changes.get_payloads(list(triplets.values()))
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
    triplets = (build_triplet(stack.project, stack.push_branch, c.change_id) for c in commits if c.change_id)
    return list(dict.fromkeys(triplets))


def _apply_failed(message: str) -> ReviewerApplyResult:
    return ReviewerApplyResult(ok=False, issues=[ReviewerApplyIssue(level="error", message=message)])


def apply_reviewer_strategy_after_push_service(
    service: GerritService,
    strategy: ReviewerStrategy,
    reviewers: list[str],
    change_refs: list[str],
) -> ReviewerApplyResult:
    """Apply lazy/overwrite reviewer strategy through the layered Gerrit service."""

    if strategy == ReviewerStrategy.PUSH or not reviewers:
        return ReviewerApplyResult(ok=True)

    outcomes: list[ReviewerApplyChangeOutcome] = []
    try:
        details_by_triplet = service.changes.get_payloads(change_refs)
    except GerritApiError as error:
        return _apply_failed(f"could not load changes: {error}")

    for triplet in change_refs:
        detail = details_by_triplet.get(triplet)
        if detail is None:
            return _apply_failed(f"could not load change {triplet}")

        footer_change_id = parse(triplet).change_id
        if footer_change_id is None:
            return _apply_failed(f"not a Gerrit triplet: {triplet}")
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
            return _apply_failed(f"could not update reviewers on {triplet}: {error}")
        outcomes.append(ReviewerApplyChangeOutcome(change_id=footer_change_id, reviewers_assigned=tuple(reviewers)))

    return ReviewerApplyResult(ok=True, outcomes=outcomes)
