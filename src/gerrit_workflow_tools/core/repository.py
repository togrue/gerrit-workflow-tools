"""The **repository** a run works in, as the run first reads it.

A run asks git once and keeps the answer for as long as it lasts: the worktree (top level,
checked-out branch, git dir), the working branch, and the stack context built on it. Nothing
here outlives its run, and nothing guesses when an answer went stale: a command that switches
branches and then needs the new one reads :class:`Worktree` again itself.
"""

from __future__ import annotations

from functools import cached_property
from pathlib import Path

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.stack_context import NoWorkingBranchError, StackContext, resolve_stack_context
from gerrit_workflow_tools.core.git_state import Worktree, rebase_underway


class Repository:
    """The repository at *cwd*, read through its *settings* snapshot, each answer asked once."""

    def __init__(self, cwd: Path | str, settings: Settings) -> None:
        self.cwd = Path(cwd)
        self.settings = settings

    @cached_property
    def worktree(self) -> Worktree:
        """Top level, checked-out branch and git dir, as the run found them."""
        return Worktree.from_cwd(self.cwd)

    @property
    def root(self) -> Path | None:
        """The repository's top level, ``None`` outside a work tree."""
        return self.worktree.toplevel

    @cached_property
    def working_branch(self) -> str | None:
        """The branch the run works on, also on a detached HEAD (:meth:`Worktree.working_branch`)."""
        return self.worktree.working_branch(settings=self.settings)

    @property
    def stack_branch(self) -> str:
        """The branch whose upstream defines the stack: the working branch, else ``HEAD``."""
        return self.working_branch or "HEAD"

    def require_stack_branch(self) -> str:
        """:attr:`stack_branch` for a run that shows the stack: on a detached HEAD only while a rebase is underway.

        Raises :class:`NoWorkingBranchError` on any other detached HEAD no branch points at.
        """
        if self.working_branch is None and not rebase_underway(self.worktree.repository_dir):
            raise NoWorkingBranchError()
        return self.stack_branch

    @cached_property
    def stack(self) -> StackContext:
        """Project, target and push branch of the working branch's stack.

        Raises ``ChangeResolutionError`` when there is none (no working branch, project
        or destination); that answer is not kept, so a caller that can do without may ask again.
        """
        return resolve_stack_context(self.cwd, self.working_branch, settings=self.settings)

    @property
    def shown_stack(self) -> StackContext:
        """:attr:`stack` for a view that shows what it can: without a working branch (a detached
        HEAD's rebase), ``ger.project`` and no target or push branch (empty)."""
        if self.working_branch is None:
            return StackContext(project=self.settings.gerrit_project or "", target_branch="", push_branch="")
        return self.stack
