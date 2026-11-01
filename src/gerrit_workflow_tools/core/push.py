"""Push plan and push refusal. Prepare, revise, and execute. Does not print or prompt."""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import quote

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.cache import GerritCache
from gerrit_workflow_tools.core.gerrit.rest import GerritApiError, GerritRest, resolve_gerrit_web_base
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.gerrit.stack_context import ChangeResolutionError, resolve_stack_context
from gerrit_workflow_tools.core.git_run import GitError, git
from gerrit_workflow_tools.core.git_state import (
    effective_gerrit_destination_branch,
    refs_for_push_branch_name,
    resolve_upstream_parsed,
)
from gerrit_workflow_tools.core.push_checks import check_existing_changes, git_or_none, rebase_check
from gerrit_workflow_tools.core.push_reviewers import (
    PushMark,
    ReviewerApplyResult,
    apply_reviewer_strategy_after_push_service,
    change_refs_for_commits,
    credential_error,
    needs_rest_assignment,
    push_marks,
)
from gerrit_workflow_tools.core.ready_calc import ReadyResult, compute_ready
from gerrit_workflow_tools.core.reviewer import ReviewerStrategy, resolve_push_reviewers
from gerrit_workflow_tools.core.stack import Commit, commits_in_range


@dataclass(frozen=True)
class PushOptions:
    reviewers: tuple[str, ...]
    strategy: ReviewerStrategy
    topic: str | None = None
    wip: bool = False
    private: bool = False


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
    unchanged: tuple[str, ...] = ()
    """Short SHAs Gerrit already has as current patchsets: push options do not reach them."""


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


def refspec_option_segments(
    *,
    reviewers: Sequence[str],
    strategy: ReviewerStrategy,
    wip: bool,
    private: bool,
    topic: str | None,
) -> list[str]:
    parts: list[str] = []
    if strategy == ReviewerStrategy.PUSH:
        parts.extend(f"r={name}" for name in reviewers)
    if wip:
        parts.append("wip")
    if private:
        parts.append("private")
    if topic:
        parts.append(f"topic={quote(topic, safe='')}")
    return parts


def push_refspec(tip: str, push_branch: str, options: PushOptions) -> str:
    ref = f"{tip}:refs/for/{push_branch}"
    parts = refspec_option_segments(
        reviewers=options.reviewers,
        strategy=options.strategy,
        wip=options.wip,
        private=options.private,
        topic=options.topic,
    )
    if parts:
        ref += "%" + ",".join(parts)
    return ref


def push_argv(plan: PushPlan) -> list[str]:
    return ["git", "push", plan.remote, push_refspec(plan.tip_sha, plan.push_branch, plan.options)]


def push_rejected_no_new_changes(stdout: str, stderr: str) -> bool:
    return "no new changes" in f"{stdout}{stderr}".lower()


def run_git_push(cmd: list[str], cwd: Path | str | None) -> Any:
    return git(*cmd[1:], cwd=cwd, check=False)


def revise(plan: PushPlan, options: PushOptions, *, settings: Settings) -> PushPlan:
    return replace(
        plan,
        options=options,
        credential_error=credential_error(options.strategy, options.reviewers, settings),
    )


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
    private: bool = False,
    no_rebase_check: bool = False,
    show_attributes: bool = False,
    update_last_pushed: bool = True,
) -> PushPlan | PushRefusal:
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
        marks = push_marks(cwd, commits, settings=settings, gerrit=gerrit) if show_attributes else None
        existing = check_existing_changes(cwd, commits, settings=settings, gerrit=gerrit)
    except (ValueError, GerritApiError) as exc:
        return PushRefusal(f"error: {exc}", warnings=warnings)
    return PushPlan(
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
    )


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
    note = "\n".join(filter(None, (_update_last_pushed(cwd, plan), _skipped_options_warning(plan)))) or None
    failed = bool(error) or (reviewers is not None and not reviewers.ok)
    return replace(captured, returncode=int(failed), error_message=error, reviewers=reviewers, warning=note)


def _skipped_options_warning(plan: PushPlan) -> str | None:
    """Gerrit applies push options only to new patchsets, so say which commits missed them."""
    options = plan.options
    sent = {
        "topic": options.topic,
        "WIP": options.wip,
        "private": options.private,
        "reviewers": options.reviewers and options.strategy == ReviewerStrategy.PUSH,
    }
    names = [name for name, on in sent.items() if on]
    if not plan.unchanged or not names:
        return None
    return f"warning: {', '.join(names)} not applied to unchanged commits: {', '.join(plan.unchanged)}"


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
    marker = f"lastPush/{plan.branch}"
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
