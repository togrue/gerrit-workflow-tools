"""Push plan and push refusal. Prepare, revise, and execute. Does not print or prompt."""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.cache import GerritCache
from gerrit_workflow_tools.core.gerrit.comments import publish_blocked
from gerrit_workflow_tools.core.gerrit.rest import GerritApiError, GerritRest, resolve_gerrit_web_base
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.gerrit.stack_context import ChangeResolutionError, resolve_stack_context
from gerrit_workflow_tools.core.git_run import GitError, git
from gerrit_workflow_tools.core.git_state import (
    LAST_PUSH_PREFIX,
    effective_gerrit_destination_branch,
    refs_for_push_branch_name,
    resolve_upstream_parsed,
)
from gerrit_workflow_tools.core.push_checks import check_existing_changes, git_or_none, rebase_check
from gerrit_workflow_tools.core.push_options import PushOptions, push_refspec
from gerrit_workflow_tools.core.push_reviewers import (
    PushMark,
    ReviewerApplyResult,
    apply_reviewer_strategy_after_push_service,
    change_refs_for_commits,
    credential_error,
    needs_rest_assignment,
    push_marks,
)
from gerrit_workflow_tools.core.push_unchanged import (
    StateUpdate,
    StateUpdateFailure,
    UnchangedChange,
    apply_updates,
    planned_updates,
)
from gerrit_workflow_tools.core.ready_calc import ReadyResult, compute_ready
from gerrit_workflow_tools.core.rebase_state import rebase_in_progress
from gerrit_workflow_tools.core.reviewer import ReviewerStrategy, resolve_push_reviewers
from gerrit_workflow_tools.core.stack import Commit, commits_in_range
from gerrit_workflow_tools.core.usage import UsageError


@dataclass(frozen=True)
class PushBoundary:
    sha: str | None
    reason: str
    short_sha: str | None
    subject: str | None
    remaining_unpushed: int


@dataclass(frozen=True)
class PushPlan:
    branch: str
    push_branch: str
    remote: str
    tip_sha: str
    commits: tuple[Commit, ...]
    marks: dict[str, PushMark] | None
    boundary: PushBoundary
    options: PushOptions
    credential_error: str | None
    warnings: tuple[str, ...]
    update_last_pushed: bool
    unchanged: tuple[UnchangedChange, ...] = ()
    """Commits Gerrit already has as current patchsets: push options reach them over REST."""
    drafts: dict[str, int] = field(default_factory=dict)
    """Your unpublished drafts per pushed commit (short SHA -> count)."""


@dataclass(frozen=True)
class PushRefusal:
    message: str
    boundary: PushBoundary | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class PushResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""
    error_message: str | None = None
    reviewers: ReviewerApplyResult | None = None
    warning: str | None = None
    updated: tuple[StateUpdate, ...] = ()
    """What was set over REST on changes Gerrit already had at that commit."""
    update_failures: tuple[StateUpdateFailure, ...] = ()


def push_argv(plan: PushPlan) -> list[str]:
    return ["git", "push", plan.remote, push_refspec(plan.tip_sha, plan.push_branch, plan.options)]


def push_rejected_no_new_changes(stdout: str, stderr: str) -> bool:
    return "no new changes" in f"{stdout}{stderr}".lower()


def run_git_push(cmd: list[str], cwd: Path | str | None) -> Any:
    return git(*cmd[1:], cwd=cwd, check=False)


def revise(plan: PushPlan, options: PushOptions, *, settings: Settings) -> PushPlan:
    """Take new push options; the drafts decision and ``--ready`` (the options line says neither) are kept.

    A line asking for WIP drops ``--ready``: the two contradict each other.
    """
    ready = plan.options.ready and not options.wip
    options = replace(options, publish_comments=plan.options.publish_comments, ready=ready)
    return replace(
        plan,
        options=options,
        credential_error=credential_error(options.strategy, options.reviewers, settings),
    )


def unchanged_short_shas(plan: PushPlan) -> set[str]:
    return {change.commit.short_sha for change in plan.unchanged}


def state_updates(plan: PushPlan) -> tuple[StateUpdate, ...]:
    """What *plan* sets over REST on the changes Gerrit already has at that commit."""
    return planned_updates(plan.options, plan.unchanged)


def publishable_drafts(plan: PushPlan) -> int:
    """Drafts this push would publish if asked: on changes it updates, while the guard allows it."""
    if publish_blocked():
        return 0
    unchanged = unchanged_short_shas(plan)
    return sum(count for sha, count in plan.drafts.items() if sha not in unchanged)


def publish_drafts_with(plan: PushPlan, publish: bool) -> PushPlan:
    """*plan* deciding to publish your drafts with the push, or to keep them. The guard always keeps."""
    if not plan.drafts:
        return plan
    return replace(plan, options=replace(plan.options, publish_comments=publish and not publish_blocked()))


def prepare_vanilla(
    cwd: Path,
    branch: str,
    *,
    settings: Settings,
    explicit_branch: bool,
    checked_out: str | None,
) -> list[str] | PushRefusal:
    parsed = resolve_upstream_parsed(cwd, branch, settings=settings)
    if explicit_branch and checked_out != branch:
        if not parsed:
            return PushRefusal(
                f"error: branch '{branch}' has no tracking upstream configured; cannot determine remote to push to"
            )
        remote_name, _rest = parsed
        return ["git", "push", remote_name, branch]
    return ["git", "push"]


def prepare(
    cwd: Path,
    branch: str,
    *,
    settings: Settings,
    gerrit: GerritRest | None = None,
    all_commits: bool = False,
    until: str | None = None,
    first_parent: bool = True,
    reviewer_flags: Sequence[str] = (),
    strategy: str | None = None,
    topic: str | None = None,
    wip: bool = False,
    ready_for_review: bool = False,
    private: bool = False,
    no_rebase_check: bool = False,
    show_attributes: bool = False,
    update_last_pushed: bool = True,
    publish_comments: bool = False,
) -> PushPlan | PushRefusal:
    if topic and "," in topic:
        raise UsageError(f"invalid topic {topic!r}: a comma separates push options")
    if rebase_in_progress(cwd):
        # The branch ref moves only when the rebase finishes: a push now sends the stack as it was before.
        raise UsageError(
            f"a rebase is in progress, and '{branch}' still points at the commits from before it; "
            "finish it (`git rebase --continue`) or `git rebase --abort`, then push"
        )
    destination = effective_gerrit_destination_branch(cwd, branch, settings=settings)
    if not destination:
        raise GitError("Internal error: Gerrit push mode without effective destination.")
    error, warnings = rebase_check(
        cwd,
        branch,
        policy=settings.push_remote_policy,
        no_rebase_check=no_rebase_check,
        settings=settings,
    )
    if error:
        return PushRefusal(error)

    reviewers = resolve_push_reviewers(cwd, branch, reviewer_flags, settings=settings)
    options = PushOptions(
        reviewers=tuple(reviewers),
        strategy=ReviewerStrategy(strategy or ReviewerStrategy.PUSH.value),
        topic=topic,
        wip=wip,
        ready=ready_for_review,
        private=private,
    )
    ready = _ready(
        cwd,
        branch,
        settings=settings,
        all_commits=all_commits,
        until=until,
        first_parent=first_parent,
    )
    boundary = _boundary(cwd, ready, head=branch)
    if not ready.push_tip_sha or not ready.push_range:
        return PushRefusal("error: nothing to push (empty ready prefix)", boundary=boundary, warnings=warnings)
    try:
        commits = commits_in_range(cwd, ready.push_range, first_parent=first_parent)
        existing = check_existing_changes(cwd, commits, settings=settings, gerrit=gerrit)
        ask_marks = show_attributes and not existing.gerrit_unavailable
        marks = push_marks(cwd, commits, settings=settings, gerrit=gerrit) if ask_marks else None
    except (ValueError, GerritApiError) as exc:
        return PushRefusal(f"error: {exc}", warnings=warnings)
    plan = PushPlan(
        branch=branch,
        push_branch=refs_for_push_branch_name(destination, settings=settings),
        remote=settings.gerrit_remote,
        tip_sha=ready.push_tip_sha,
        commits=tuple(commits),
        marks=marks,
        boundary=boundary,
        options=options,
        credential_error=credential_error(options.strategy, options.reviewers, settings),
        warnings=(*warnings, *existing.warnings),
        update_last_pushed=update_last_pushed,
        unchanged=existing.unchanged,
        drafts=dict(existing.drafts),
    )
    return publish_drafts_with(plan, publish_comments)


def execute(
    cwd: Path,
    plan: PushPlan | Sequence[str],
    *,
    settings: Settings,
    gerrit: GerritRest | None = None,
) -> PushResult:
    if not isinstance(plan, PushPlan):
        return _captured(run_git_push(list(plan), cwd))
    argv = push_argv(plan)
    captured = _captured(run_git_push(argv, cwd))
    rejected = push_rejected_no_new_changes(captured.stdout, captured.stderr)
    if captured.returncode != 0 and not rejected:
        return captured
    if rejected:
        captured = PushResult(returncode=0, stdout="nothing to push: Gerrit already has these commits")
    _forget_pushed_changes(cwd, plan, settings=settings, gerrit=gerrit)
    reviewers, error = _apply_reviewers(cwd, plan, settings=settings, gerrit=gerrit)
    updates = state_updates(plan)
    failures = apply_updates(GerritService.from_cwd(cwd, settings=settings, rest=gerrit), updates) if updates else []
    failed_updates = {failure.update for failure in failures}
    note = "\n".join(filter(None, (_update_last_pushed(cwd, plan), _unreached_options_warning(plan, rejected))))
    failed = bool(error) or bool(failures) or (reviewers is not None and not reviewers.ok)
    return replace(
        captured,
        returncode=int(failed),
        error_message=error,
        reviewers=reviewers,
        warning=note or None,
        updated=tuple(update for update in updates if update not in failed_updates),
        update_failures=tuple(failures),
    )


def _unreached_options_warning(plan: PushPlan, rejected: bool) -> str | None:
    """Gerrit took no new patch set and ger could not read the changes, so options set nothing."""
    options = plan.options
    pushed_reviewers = bool(options.reviewers) and options.strategy == ReviewerStrategy.PUSH
    asked = bool(options.topic) or options.wip or options.ready or options.private or pushed_reviewers
    if not rejected or plan.unchanged or not asked:
        return None
    return (
        "warning: push options not applied: Gerrit already has these commits, and without Gerrit's "
        "view of the changes ger cannot set them over REST (check gerrit.user and gerrit.token)"
    )


def _forget_pushed_changes(cwd: Path, plan: PushPlan, *, settings: Settings, gerrit: GerritRest | None) -> None:
    """Drop what the cache knows about the pushed changes, "no such change" verdicts included.

    A failure here must not turn a successful push into a failed one.
    """
    with contextlib.suppress(ValueError, OSError, sqlite3.Error, ChangeResolutionError):
        web_base = gerrit.web_base if gerrit is not None else resolve_gerrit_web_base(settings)
        refs = change_refs_for_commits(cwd, plan.commits, settings=settings)
        GerritCache.for_web_base(web_base).invalidate_changes(refs)


def _ready(
    cwd: Path,
    branch: str,
    *,
    settings: Settings,
    all_commits: bool,
    until: str | None,
    first_parent: bool,
) -> ReadyResult:
    project = settings.gerrit_project or ""
    with contextlib.suppress(Exception):
        project = resolve_stack_context(cwd, settings=settings).project
    return compute_ready(
        cwd,
        branch=branch,
        head=branch,
        all_commits=all_commits,
        until=until,
        first_parent=first_parent,
        stop_pattern=settings.stop_pattern,
        project=project,
        settings=settings,
        web_base=settings.gerrit_web_url,
    )


def _boundary(cwd: Path, ready: ReadyResult, *, head: str) -> PushBoundary:
    sha = ready.boundary_sha
    short_sha = git_or_none(cwd, "rev-parse", "--short", sha) if sha else None
    subject = git_or_none(cwd, "show", "-s", "--format=%s", sha) if sha else None
    count = git_or_none(cwd, "rev-list", "--count", f"{sha}..{head}") if sha else None
    # The boundary commit itself stays unpushed, and `sha..head` leaves it out.
    remaining = int(count) + 1 if count and count.isdigit() else 0
    return PushBoundary(sha, ready.boundary_reason, short_sha, subject, remaining)


def _apply_reviewers(
    cwd: Path,
    plan: PushPlan,
    *,
    settings: Settings,
    gerrit: GerritRest | None,
) -> tuple[ReviewerApplyResult | None, str | None]:
    if not needs_rest_assignment(plan.options.strategy, plan.options.reviewers):
        return None, None
    result = apply_reviewer_strategy_after_push_service(
        GerritService.from_cwd(cwd, settings=settings, rest=gerrit),
        plan.options.strategy,
        list(plan.options.reviewers),
        change_refs_for_commits(cwd, plan.commits, settings=settings),
    )
    return result, None


def _update_last_pushed(cwd: Path, plan: PushPlan) -> str | None:
    if not plan.update_last_pushed:
        return None
    marker = f"{LAST_PUSH_PREFIX}{plan.branch}"
    try:
        git("branch", "-f", marker, plan.tip_sha, cwd=cwd)
    except GitError as exc:
        return f"warning: could not update {marker}: {exc}"
    return None


def _captured(proc: Any) -> PushResult:
    return PushResult(
        returncode=int(proc.returncode),
        stdout=_text(getattr(proc, "stdout", "")),
        stderr=_text(getattr(proc, "stderr", "")),
    )


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return ""
