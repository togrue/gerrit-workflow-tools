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
    removable_reviewer_ids,
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
            bool(detail.get("is_private")),
        )
    return marks


def change_refs_for_commits(cwd: Path, commits: Sequence[Commit], *, settings: Settings) -> list[str]:
    stack = resolve_stack_context(cwd, settings=settings)
    triplets = (build_triplet(stack.project, stack.push_branch, c.change_id) for c in commits if c.change_id)
    return list(dict.fromkeys(triplets))


def _not_applied(message: str) -> ReviewerApplyIssue:
    """A warning, not an error: reviewers are applied after the push, which already succeeded."""
    return ReviewerApplyIssue(level="warning", message=f"pushed, but {message}")


def _apply_failed(message: str) -> ReviewerApplyResult:
    return ReviewerApplyResult(ok=False, issues=[_not_applied(message)])


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
    issues: list[ReviewerApplyIssue] = []
    kept: dict[str, list[str]] = {}
    try:
        details_by_triplet = service.changes.get_payloads(change_refs)
    except GerritApiError as error:
        return _apply_failed(f"could not load changes: {error}")

    # Named reviewers stay: Gerrit reports nothing for one already on the change, so removing
    # them after the add would take them off again.
    wanted = {name.casefold() for name in reviewers}
    for triplet in change_refs:
        detail = details_by_triplet.get(triplet)
        if detail is None:
            issues.append(_not_applied(f"could not load change {triplet}"))
            continue

        footer_change_id = parse(triplet).change_id
        if footer_change_id is None:
            issues.append(_not_applied(f"not a Gerrit triplet: {triplet}"))
            continue
        existing = reviewer_accounts_from_change_info(detail)
        if strategy == ReviewerStrategy.LAZY and existing:
            outcomes.append(ReviewerApplyChangeOutcome(change_id=footer_change_id, reviewers_assigned=()))
            continue

        remove: list[int] = []
        if strategy == ReviewerStrategy.OVERWRITE:
            reviewers_only = reviewer_accounts_from_change_info(detail, roles=("REVIEWER",))
            # Gerrit refuses the delete for a reviewer the caller may not remove
            # (a negative vote, for a change owner): keep them and say so.
            removable = removable_reviewer_ids(detail)
            for account in reviewers_only:
                if account.account_id is None or account.slug.casefold() in wanted:
                    continue
                if removable is None or account.account_id in removable:
                    remove.append(account.account_id)
                else:
                    kept.setdefault(account.slug, []).append(footer_change_id)
        try:
            service.changes.set_reviewers(triplet, add=reviewers, remove=remove)
        except GerritApiError as error:
            issues.append(_not_applied(f"could not update reviewers on {triplet}: {error}"))
            continue
        outcomes.append(ReviewerApplyChangeOutcome(change_id=footer_change_id, reviewers_assigned=tuple(reviewers)))

    ok = not issues
    for slug, change_ids in kept.items():
        where = "1 change" if len(change_ids) == 1 else f"{len(change_ids)} changes"
        issues.append(
            ReviewerApplyIssue(
                "warning",
                f"kept {slug} on {where}: Gerrit does not let you remove them "
                "(a negative vote needs the Remove Reviewer right)",
            )
        )
    if strategy == ReviewerStrategy.LAZY and ok and not any(outcome.reviewers_assigned for outcome in outcomes):
        names = ", ".join(reviewers)
        issues.append(
            ReviewerApplyIssue("warning", f"every change already has reviewers; {names} not added or checked")
        )
    return ReviewerApplyResult(ok=ok, issues=issues, outcomes=outcomes)
