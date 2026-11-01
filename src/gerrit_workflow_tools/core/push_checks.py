"""What a push looks at before it runs. Does not print or prompt."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.change_view import ChangeView
from gerrit_workflow_tools.core.gerrit.rest import GerritApiError, GerritRest
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.gerrit.stack_context import (
    ChangeResolutionError,
    build_triplet,
    resolve_stack_context,
)
from gerrit_workflow_tools.core.git_run import GitError, git, git_out
from gerrit_workflow_tools.core.git_state import head_is_linear_on_remote_gerrit_target
from gerrit_workflow_tools.core.reviewer import gerrit_credentials_configured
from gerrit_workflow_tools.core.stack import Commit


_REBASE_HINT = "Hint: run `ger rebase --onto-remote` to replay your commits on top of the latest target branch."


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


def abandoned_change_warnings(
    cwd: Path,
    commits: Sequence[Commit],
    *,
    settings: Settings,
    gerrit: GerritRest | None,
) -> tuple[str, ...]:
    """One warning per commit whose change is abandoned on Gerrit.

    Best effort: a push works without REST, so no credentials or a failed lookup means no warning.
    """
    if gerrit is None and not gerrit_credentials_configured(settings):
        return ()
    try:
        stack = resolve_stack_context(cwd, settings=settings)
        triplets = {c.sha: build_triplet(stack.project, stack.push_branch, c.change_id) for c in commits if c.change_id}
        service = GerritService.from_cwd(cwd, settings=settings, rest=gerrit)
        payloads = service.changes.get_payloads(list(triplets.values()))
    except (ValueError, GerritApiError, ChangeResolutionError):
        return ()
    warnings: list[str] = []
    for commit in commits:
        payload = payloads.get(triplets.get(commit.sha, ""))
        view = ChangeView.from_payload(payload) if payload else None
        if view is not None and (view.status or "").upper() == "ABANDONED":
            warnings.append(
                f"warning: {commit.short_sha} targets abandoned change {view.number}; "
                "restore it on Gerrit or drop the commit"
            )
    return tuple(warnings)
