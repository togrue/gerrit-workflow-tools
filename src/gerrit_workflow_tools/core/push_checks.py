"""What a push looks at before it runs. Does not print or prompt."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.change_view import ChangeView
from gerrit_workflow_tools.core.gerrit.rest import GerritApiError, GerritRest, GerritUnavailableError
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.gerrit.stack_context import (
    ChangeResolutionError,
    build_triplet,
    resolve_stack_context,
)
from gerrit_workflow_tools.core.git_run import GitError, UnknownRevisionError, git, git_out
from gerrit_workflow_tools.core.git_state import head_is_linear_on_remote_gerrit_target, resolve_working_branch
from gerrit_workflow_tools.core.push_unchanged import UnchangedChange
from gerrit_workflow_tools.core.reviewer import gerrit_credentials_configured
from gerrit_workflow_tools.core.stack import Commit


_REBASE_HINT = "Hint: run `ger rebase --onto-remote` to replay your commits on top of the latest target branch."


def resolve_push_branch(cwd: Path, branch_arg: str | None, *, settings: Settings) -> str:
    """Return the local branch ``ger push`` operates on."""
    if branch_arg:
        try:
            git_out("rev-parse", "--verify", branch_arg, cwd=cwd)
        except GitError as exc:
            raise UnknownRevisionError(f"branch {branch_arg!r} not found") from exc
        return branch_arg
    branch = resolve_working_branch(cwd, settings=settings)
    if branch is None:
        raise GitError(
            "ger push requires a branch (detached HEAD with no local branch at this commit). "
            "Check out a branch first, or pass --branch."
        )
    return branch


def git_or_none(cwd: Path, *args: str) -> str | None:
    try:
        return git_out(*args, cwd=cwd)
    except GitError:
        return None


def rebase_check(
    cwd: Path,
    branch: str,
    *,
    policy: str,
    no_rebase_check: bool,
    settings: Settings,
) -> tuple[str | None, tuple[str, ...]]:
    if no_rebase_check or policy == "ignore-not-rebased":
        return None, ()
    remote_name = settings.gerrit_remote
    try:
        git("fetch", remote_name, cwd=cwd)
    except GitError as exc:
        return None, (f"warning: could not fetch `{remote_name}`; skipping remote rebase check: {exc}",)
    try:
        ok, onto = head_is_linear_on_remote_gerrit_target(cwd, branch, head=branch, settings=settings)
    except GitError as exc:
        return None, (f"warning: could not compare HEAD to remote target; skipping remote rebase check: {exc}",)
    if ok:
        return None, ()
    message = (
        "Local HEAD is not based directly on the current Gerrit target branch after fetch "
        f"({_rebase_detail(cwd, branch, onto)}). {_REBASE_HINT}"
    )
    if policy == "error-not-rebased":
        return f"error: {message}", ()
    return None, (f"warning: {message}",)


def _rebase_detail(cwd: Path, head: str, onto: str) -> str:
    short_onto = (
        git_or_none(cwd, "rev-parse", "--abbrev-ref", onto) or git_or_none(cwd, "rev-parse", "--short", onto) or onto
    )
    tip = git_or_none(cwd, "rev-parse", "--short", onto)
    fork = git_or_none(cwd, "merge-base", head, onto)
    fork_s = git_or_none(cwd, "rev-parse", "--short", fork) if fork else None
    if tip and fork_s:
        return (
            f"tip of `{short_onto}` ({tip}) is not an ancestor of HEAD "
            f"(fork at {fork_s}; rebase onto the fetched target to linearize)"
        )
    return f"tip of `{short_onto}` is not an ancestor of HEAD"


class ExistingChanges(NamedTuple):
    """What Gerrit already holds for the commits about to be pushed."""

    warnings: tuple[str, ...] = ()
    unchanged: tuple[UnchangedChange, ...] = ()
    """Commits that already are their change's current patchset, with that change's state."""
    drafts: tuple[tuple[str, int], ...] = ()
    """``(short SHA, count)`` of your unpublished drafts, for commits whose change has any."""
    gerrit_unavailable: bool = False
    """Gerrit could not be asked at all; :attr:`warnings` says why. Nothing else here is known."""


def check_existing_changes(
    cwd: Path,
    commits: Sequence[Commit],
    *,
    settings: Settings,
    gerrit: GerritRest | None,
) -> ExistingChanges:
    """Warn about abandoned changes and list the commits Gerrit will not see as new.

    Best effort: a push works without REST, so no credentials or a failed lookup finds nothing.
    """
    if gerrit is None and not gerrit_credentials_configured(settings):
        return ExistingChanges()
    try:
        stack = resolve_stack_context(cwd, settings=settings)
        triplets = {c.sha: build_triplet(stack.project, stack.push_branch, c.change_id) for c in commits if c.change_id}
        service = GerritService.from_cwd(cwd, settings=settings, rest=gerrit)
        payloads = service.changes.get_payloads(list(triplets.values()))
    except GerritUnavailableError as error:
        return ExistingChanges(
            warnings=(f"warning: {error}; the preview shows no Gerrit state",), gerrit_unavailable=True
        )
    except (ValueError, GerritApiError, ChangeResolutionError):
        return ExistingChanges()
    warnings: list[str] = []
    unchanged: list[UnchangedChange] = []
    views = {sha: ChangeView.from_payload(p) for sha, p in ((s, payloads.get(t)) for s, t in triplets.items()) if p}
    drafts = _draft_counts(service, commits, triplets, views)
    for commit in commits:
        view = views.get(commit.sha)
        if view is not None and (view.status or "").upper() == "ABANDONED":
            warnings.append(
                f"warning: {commit.short_sha} targets abandoned change {view.number}; "
                "restore it on Gerrit or drop the commit"
            )
        elif view is not None and view.current_revision == commit.sha.lower():
            unchanged.append(UnchangedChange.of(commit, triplets[commit.sha], view))
    return ExistingChanges(tuple(warnings), tuple(unchanged), drafts)


def _draft_counts(
    service: GerritService, commits: Sequence[Commit], triplets: dict[str, str], views: dict[str, ChangeView]
) -> tuple[tuple[str, int], ...]:
    """Your drafts per commit: one ``has:draft`` query, then a drafts GET only where it found some.

    Best effort, like the rest of this check: a failed lookup finds no drafts.
    """
    by_number = {view.number: sha for sha, view in views.items() if view.number is not None}
    if not by_number:
        return ()
    query = "has:draft (" + " OR ".join(f"change:{number}" for number in by_number) + ")"
    try:
        rows = service.rest.query_changes(query, n=len(by_number))
        drafted = [by_number[n] for n in (ChangeView.from_payload(r).number for r in rows) if n in by_number]
        counts = {sha: sum(map(len, service.comments.get_drafts(triplets[sha]).values())) for sha in drafted}
    except GerritApiError:
        return ()
    return tuple((c.short_sha, counts[c.sha]) for c in commits if counts.get(c.sha))
