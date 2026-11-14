"""Stack context: the project, target branch and push branch a stack belongs to, and triplets built from them."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit_project_id import resolve_gerrit_project_name
from gerrit_workflow_tools.core.git_state import (
    effective_gerrit_destination_branch,
    refs_for_push_branch_name,
    resolve_working_branch,
)


class ChangeResolutionError(RuntimeError):
    """Changeish could not be resolved (not found, invalid input, missing stack context)."""


@dataclass(frozen=True)
class StackContext:
    """Repo/branch context for triplet building and Change-Id narrowing."""

    project: str
    target_branch: str
    push_branch: str


def build_triplet(project: str, branch: str, change_id: str) -> str:
    """Build a Gerrit REST triplet from stack context and a footer Change-Id."""
    return f"{project}~{branch}~{change_id}"


def _stack_branch_name(cwd: Path | str | None, branch: str | None, *, settings: Settings) -> str:
    if branch is not None:
        return branch
    working = resolve_working_branch(cwd, settings=settings)
    if working:
        return working
    raise ChangeResolutionError("cannot determine working branch (detached HEAD with no branch context)")


def stack_project(cwd: Path | str | None, branch: str | None = None, *, settings: Settings) -> str:
    """The stack context's project, else ``gerrit.project`` (for work that needs no push destination)."""
    try:
        return resolve_stack_context(cwd, branch, settings=settings).project
    except Exception:  # pylint: disable=broad-exception-caught
        return settings.gerrit_project or ""


def resolve_stack_context(cwd: Path | str | None, branch: str | None = None, *, settings: Settings) -> StackContext:
    """Resolve project, target branch, and push branch for the stack of *branch* (the working branch).

    Asks git each time. A run asks once and keeps the answer: ``run.stack``.
    """
    branch_name = _stack_branch_name(cwd, branch, settings=settings)
    project = resolve_gerrit_project_name(cwd, settings=settings)
    if not project:
        remote = settings.gerrit_remote
        raise ChangeResolutionError(
            f"cannot resolve Gerrit project name; set `gerrit.project` or configure remote `{remote}` "
            "with a parseable URL"
        )

    target = effective_gerrit_destination_branch(cwd, branch_name, settings=settings)
    if not target:
        remote = settings.gerrit_remote
        raise ChangeResolutionError(
            f"No Gerrit destination branch for branch {branch_name!r}. "
            f"Set upstream to a branch on `{remote}` (`gerrit.remote`), "
            f"e.g. `git branch --set-upstream-to={remote}/<branch>`, "
            f"or configure `branch.{branch_name}.gerritTarget`."
        )

    push_branch = refs_for_push_branch_name(target, settings=settings)
    return StackContext(project=project, target_branch=target, push_branch=push_branch)
