"""Core reviewer strategy operations for Gerrit push flows."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from gerrit_workflow_tools.core.change_id import change_id_of_rev
from gerrit_workflow_tools.core.changeish import parse
from gerrit_workflow_tools.core.config import ConfigError, Settings
from gerrit_workflow_tools.core.gerrit.rest import GerritApiError, resolve_gerrit_web_base
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.gerrit.stack_context import build_triplet
from gerrit_workflow_tools.core.gerrit_project_id import resolve_gerrit_project_name
from gerrit_workflow_tools.core.reviewer import (
    ReviewerStrategy,
    gerrit_credentials_configured,
    removable_reviewer_ids,
    reviewer_account_ids,
    reviewer_accounts_from_change_info,
    reviewer_handles,
)
from gerrit_workflow_tools.core.run_context import RunContext
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


def change_ref_of(cwd: Path, push_branch: str, rev: str, *, settings: Settings) -> str | None:
    """The *push_branch* triplet for the change *rev* carries; ``None`` without a Change-Id or project.

    What the push prompt's reviewer completion asks Gerrit about: never the bare Change-Id,
    which Gerrit answers with 404 ("Multiple changes found") once it is on a second branch.
    """
    change_id = change_id_of_rev(cwd, rev)
    project = resolve_gerrit_project_name(cwd, settings=settings) if change_id else None
    return build_triplet(project, push_branch, change_id) if change_id and project else None


def unknown_reviewers(
    service: GerritService, strategy: ReviewerStrategy, reviewers: Sequence[str], *, present: Iterable[str] = ()
) -> tuple[str, ...]:
    """The *reviewers* Gerrit has no account for: what a push must not send, asked before it sends anything.

    Only for the ``push`` strategy, where ``%r=`` and the reviewer batch on changes Gerrit already
    has take accounts; ``lazy`` and ``overwrite`` add over REST after the push, where a group is a
    reviewer too (``spec/commands/push.md``). A name already on a pushed change (*present*) is
    known without a request. Best effort: without credentials, or when Gerrit cannot be asked,
    nothing is known and nothing is refused, and Gerrit judges the push itself.
    """
    known = {name.casefold() for name in present}
    asked = [name for name in reviewers if name.casefold() not in known]
    if strategy != ReviewerStrategy.PUSH or not asked or not service.credentials_configured:
        return ()
    try:
        ids = reviewer_account_ids(service.rest, asked)
    except GerritApiError:
        return ()
    return tuple(name for name, account_id in ids.items() if account_id is None)


def needs_rest_assignment(strategy: ReviewerStrategy, reviewers: Sequence[str]) -> bool:
    """True when lazy or overwrite must assign reviewers after the push."""
    return strategy in (ReviewerStrategy.LAZY, ReviewerStrategy.OVERWRITE) and bool(reviewers)


def credential_error(strategy: ReviewerStrategy, reviewers: Sequence[str], settings: Settings) -> str | None:
    """Why lazy/overwrite cannot call REST, or ``None`` when they can or need not."""
    if not needs_rest_assignment(strategy, reviewers):
        return None
    if not gerrit_credentials_configured(settings):
        return (
            "error: lazy/overwrite reviewer strategies need Gerrit REST; set ger.user and ger.token (or ger.password)"
        )
    try:
        resolve_gerrit_web_base(settings)
    except ValueError as exc:
        return f"error: {exc}"
    return None


def push_marks(run: RunContext, rows: Sequence[Commit]) -> dict[str, PushMark]:
    settings = run.settings
    stack = run.stack
    triplets = {
        row.change_id: build_triplet(stack.project, stack.push_branch, row.change_id) for row in rows if row.change_id
    }
    if not triplets:
        return {}
    resolve_gerrit_web_base(settings)
    if not gerrit_credentials_configured(settings):
        raise ConfigError(
            "Gerrit credentials are not configured; set ger.user and ger.token (or ger.password) for REST access."
        )
    by_triplet = run.gerrit.changes.get_payloads(list(triplets.values()))
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


def change_refs_for_commits(run: RunContext, commits: Sequence[Commit]) -> list[str]:
    stack = run.stack
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

    # Overwrite compares people by account id: a reviewer named by email, full name or id is the
    # same person as the slug Gerrit reports. Removals need to know every wanted person: a name
    # that resolves to no single account (a group, whose members Gerrit adds) or a failed lookup
    # turns them off for the run, and everyone present stays.
    wanted_ids: dict[str, int | None] = {}
    lookup_failed = False
    if strategy == ReviewerStrategy.OVERWRITE:
        try:
            wanted_ids = reviewer_account_ids(
                service.rest, reviewers, known=reviewer_handles(details_by_triplet.values())
            )
        except GerritApiError as error:
            lookup_failed = True
            issues.append(_not_applied(f"removed no reviewers: could not look up {', '.join(reviewers)}: {error}"))
    keep_ids = {account_id for account_id in wanted_ids.values() if account_id is not None}
    unresolved = [name for name in reviewers if wanted_ids.get(name) is None]
    held: dict[str, list[str]] = {}
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

        add = list(reviewers)
        remove: list[int] = []
        held_here: list[str] = []
        if strategy == ReviewerStrategy.OVERWRITE:
            reviewers_only = reviewer_accounts_from_change_info(detail, roles=("REVIEWER",))
            present_ids = {account.account_id for account in reviewers_only if account.account_id is not None}
            # Only who is missing is added; asking Gerrit again for someone present is needless.
            add = [name for name in reviewers if name in unresolved or wanted_ids[name] not in present_ids]
            # Gerrit refuses the delete for a reviewer the caller may not remove
            # (a negative vote, for a change owner): keep them and say so.
            removable = removable_reviewer_ids(detail)
            for account in reviewers_only:
                if account.account_id is None or account.account_id in keep_ids:
                    continue
                if unresolved:
                    held_here.append(account.slug)
                elif removable is None or account.account_id in removable:
                    remove.append(account.account_id)
                else:
                    kept.setdefault(account.slug, []).append(footer_change_id)
        if add or remove:
            try:
                service.changes.set_reviewers(triplet, add=add, remove=remove)
            except GerritApiError as error:
                issues.append(_not_applied(f"could not update reviewers on {triplet}: {error}"))
                continue
        for slug in held_here:
            held.setdefault(slug, []).append(footer_change_id)
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
    if held and not lookup_failed:
        held_on = len({change_id for change_ids in held.values() for change_id in change_ids})
        where = "1 change" if held_on == 1 else f"{held_on} changes"
        issues.append(
            ReviewerApplyIssue(
                "warning",
                f"kept {', '.join(held)} on {where}: {', '.join(unresolved)} names no single account "
                "(a group?), so overwrite cannot tell who to remove",
            )
        )
    if strategy == ReviewerStrategy.LAZY and ok and not any(outcome.reviewers_assigned for outcome in outcomes):
        names = ", ".join(reviewers)
        issues.append(
            ReviewerApplyIssue("warning", f"every change already has reviewers; {names} not added or checked")
        )
    return ReviewerApplyResult(ok=ok, issues=issues, outcomes=outcomes)
