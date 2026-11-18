"""Push plan and push refusal. Prepare, revise, and execute. Does not print or prompt."""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from gerrit_workflow_tools.core.change_id import change_id_of_rev
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.comments import publish_blocked
from gerrit_workflow_tools.core.gerrit.rest import GerritApiError
from gerrit_workflow_tools.core.gerrit.stack_context import ChangeResolutionError, build_triplet
from gerrit_workflow_tools.core.gerrit_project_id import resolve_gerrit_project_name
from gerrit_workflow_tools.core.git_run import GitError, git
from gerrit_workflow_tools.core.git_state import (
    LAST_PUSH_PREFIX,
    effective_gerrit_destination_branch,
    refs_for_push_branch_name,
    resolve_upstream_parsed,
)
from gerrit_workflow_tools.core.push_checks import (
    PushBoundary,
    check_existing_changes,
    push_boundary,
    push_ready,
    rebase_check,
)
from gerrit_workflow_tools.core.push_options import PushOptions, push_refspec
from gerrit_workflow_tools.core.push_result import PushResult, gerrit_rejection, push_rejected_no_new_changes, result_of
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
    UnchangedChange,
    apply_updates,
    planned_updates,
    unreached_options_warning,
)
from gerrit_workflow_tools.core.rebase_state import rebase_underway
from gerrit_workflow_tools.core.reviewer import ReviewerStrategy, resolve_push_reviewers
from gerrit_workflow_tools.core.run_context import RunContext
from gerrit_workflow_tools.core.stack import Commit, commits_in_range
from gerrit_workflow_tools.core.usage import UsageError


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
    outdated: tuple[Commit, ...] = ()
    """Commits Gerrit already has as older patchsets: not sent, so Gerrit keeps its newer one."""
    drafts: dict[str, int] = field(default_factory=dict)
    """Your unpublished drafts per pushed commit (short SHA -> count)."""
    wip_from: str | None = None
    """The first commit pushed as work-in-progress; those below it go up ready. ``None``: the options
    decide for every commit."""


@dataclass(frozen=True)
class PushRefusal:
    message: str
    boundary: PushBoundary | None = None
    warnings: tuple[str, ...] = ()


def change_ref_of(cwd: Path, plan: PushPlan, rev: str, *, settings: Settings) -> str | None:
    """The push branch's triplet for the change *rev* carries; ``None`` without a Change-Id or project.

    What the push prompt's reviewer completion asks Gerrit about: never the bare Change-Id,
    which Gerrit answers with 404 ("Multiple changes found") once it is on a second branch.
    """
    change_id = change_id_of_rev(cwd, rev)
    project = resolve_gerrit_project_name(cwd, settings=settings) if change_id else None
    return build_triplet(project, plan.push_branch, change_id) if change_id and project else None


def push_argv(plan: PushPlan) -> list[str]:
    return ["git", "push", plan.remote, push_refspec(plan.tip_sha, plan.push_branch, plan.options)]


def wip_commits(plan: PushPlan) -> tuple[Commit, ...]:
    """The commits *plan* pushes as work-in-progress: from :attr:`PushPlan.wip_from` up, else all or none."""
    if plan.wip_from is None:
        return plan.commits if plan.options.wip else ()
    shas = [commit.sha for commit in plan.commits]
    return plan.commits[shas.index(plan.wip_from) :]


def push_argvs(plan: PushPlan) -> list[list[str]]:
    """The ``git push`` runs for *plan*: one, or two when only its top is WIP.

    Gerrit applies ``%wip`` to every change a push creates or updates, so a ready lower part
    goes first and the WIP top second (where the lower commits are already Gerrit's, unchanged).
    """
    wip = wip_commits(plan)
    if plan.wip_from is None or not wip:
        return [push_argv(plan)]
    as_wip = replace(plan.options, wip=True, ready=False)
    if len(wip) == len(plan.commits):
        return [["git", "push", plan.remote, push_refspec(plan.tip_sha, plan.push_branch, as_wip)]]
    ready_tip = plan.commits[len(plan.commits) - len(wip) - 1].sha
    as_ready = replace(plan.options, wip=False)
    return [
        ["git", "push", plan.remote, push_refspec(ready_tip, plan.push_branch, as_ready)],
        ["git", "push", plan.remote, push_refspec(plan.tip_sha, plan.push_branch, as_wip)],
    ]


def run_git_push(cmd: list[str], cwd: Path | str | None) -> Any:
    return git(*cmd[1:], cwd=cwd, check=False)


def revise(plan: PushPlan, options: PushOptions, *, settings: Settings) -> PushPlan:
    """Take new push options; the drafts decision, ``--ready`` and ``--no-private`` (the options line says
    none of them) are kept.

    A line asking for WIP drops ``--ready``, one asking for private drops ``--no-private``: each pair contradicts.
    """
    ready = plan.options.ready and not options.wip
    public = plan.options.remove_private and not options.private
    options = replace(options, publish_comments=plan.options.publish_comments, ready=ready, remove_private=public)
    return replace(
        plan,
        options=options,
        credential_error=credential_error(options.strategy, options.reviewers, settings),
    )


def changes_nothing(plan: PushPlan) -> bool:
    """Nothing to send and nothing to set over REST: the push would leave Gerrit as it is."""
    rest = state_updates(plan) or needs_rest_assignment(plan.options.strategy, plan.options.reviewers)
    return not sent_commits(plan) and not rest


def sent_commits(plan: PushPlan) -> tuple[Commit, ...]:
    """The commits *plan* gives Gerrit as new patch sets: all but those already one of their change's patch sets."""
    known = {change.commit.sha for change in plan.unchanged} | {commit.sha for commit in plan.outdated}
    return tuple(commit for commit in plan.commits if commit.sha not in known)


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
    run: RunContext,
    branch: str,
    *,
    all_commits: bool = False,
    until: str | None = None,
    until_sha: str | None = None,
    first_parent: bool = True,
    reviewer_flags: Sequence[str] = (),
    strategy: str | None = None,
    topic: str | None = None,
    wip: bool = False,
    ready_for_review: bool = False,
    private: bool = False,
    remove_private: bool = False,
    no_rebase_check: bool = False,
    show_attributes: bool = False,
    update_last_pushed: bool = True,
    publish_comments: bool = False,
    force: bool = False,
    wip_from: str | None = None,
) -> PushPlan | PushRefusal:
    cwd, settings = run.cwd, run.settings
    if topic and "," in topic:
        raise UsageError(f"invalid topic {topic!r}: a comma separates push options")
    if rebase_underway(run.worktree.repository_dir):
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
        remove_private=remove_private,
    )
    ready = push_ready(
        run,
        branch,
        all_commits=all_commits,
        until=until,
        until_sha=until_sha,
        first_parent=first_parent,
    )
    boundary = push_boundary(cwd, ready, head=branch)
    if all_commits and wip_from is None and not (wip or ready_for_review):
        # Past the ready boundary on purpose: what it held back goes up, but as work-in-progress.
        held = push_ready(run, branch, all_commits=False, until=until, until_sha=until_sha, first_parent=first_parent)
        wip_from = held.boundary_sha
    if not ready.push_tip_sha or not ready.push_range:
        return PushRefusal("error: nothing to push (empty ready prefix)", boundary=boundary, warnings=warnings)
    try:
        commits = commits_in_range(cwd, ready.push_range, first_parent=first_parent)
        existing = check_existing_changes(run, commits)
        ask_marks = show_attributes and not existing.gerrit_unavailable
        marks = push_marks(run, commits) if ask_marks else None
    except (ValueError, GerritApiError) as exc:
        return PushRefusal(f"error: {exc}", warnings=warnings)
    if wip_from is not None and wip_from not in {commit.sha for commit in commits}:
        if not all_commits:
            raise UsageError(f"--wip-from {wip_from[:8]} is not among the commits this push sends")
        wip_from = None
    if existing.diverged and not force:
        first = existing.diverged[0]
        return PushRefusal(
            f"error: {'; '.join(d.describe() for d in existing.diverged)}. Merge it into yours first "
            f"(`ger checkout change:{first.number} --update`), or replace it anyway with --force",
            boundary=boundary,
            warnings=warnings,
        )
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
        outdated=existing.outdated,
        drafts=dict(existing.drafts),
        wip_from=wip_from,
    )
    return publish_drafts_with(plan, publish_comments)


def execute(run: RunContext, plan: PushPlan | Sequence[str]) -> PushResult:
    cwd = run.cwd
    if not isinstance(plan, PushPlan):
        return result_of(run_git_push(list(plan), cwd))
    sent = bool(sent_commits(plan))  # else Gerrit says "no new changes": not run
    captured, rejected = PushResult(0), True
    for argv in push_argvs(plan) if sent else []:
        result = result_of(run_git_push(argv, cwd))
        if push_rejected_no_new_changes(result.stdout, result.stderr):
            continue  # this run had nothing new; another may have
        if result.returncode != 0:  # git's code passes through (spec/exit-codes.md)
            return replace(result, error_message=gerrit_rejection(result.stderr))
        captured, rejected = result, False
    if rejected:
        captured = PushResult(returncode=0, nothing_sent=True)
    _forget_pushed_changes(run, plan)
    reviewers, error = _apply_reviewers(run, plan)
    updates = state_updates(plan)
    failures = apply_updates(run.gerrit, updates) if updates else []
    failed_updates = {failure.update for failure in failures}
    unreached = unreached_options_warning(plan.options, plan.unchanged, rejected=rejected)
    note = "\n".join(filter(None, (_update_last_pushed(cwd, plan), unreached)))
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


def _forget_pushed_changes(run: RunContext, plan: PushPlan) -> None:
    """Drop what the cache knows about the changes this push sent, "no such change" verdicts included.

    Only those: a commit Gerrit already had did not move, and what is set on it over REST
    drops it by itself. A failure here must not turn a successful push into a failed one.
    """
    with contextlib.suppress(ValueError, OSError, sqlite3.Error, ChangeResolutionError):
        refs = change_refs_for_commits(run, sent_commits(plan))
        run.gerrit.cache.invalidate_changes(refs)


def _apply_reviewers(run: RunContext, plan: PushPlan) -> tuple[ReviewerApplyResult | None, str | None]:
    if not needs_rest_assignment(plan.options.strategy, plan.options.reviewers):
        return None, None
    result = apply_reviewer_strategy_after_push_service(
        run.gerrit,
        plan.options.strategy,
        list(plan.options.reviewers),
        change_refs_for_commits(run, plan.commits),
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
