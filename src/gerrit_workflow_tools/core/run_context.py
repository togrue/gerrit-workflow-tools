"""The **run context**: one command run, or one request ``ger serve`` answers.

Everything a run reads once and then shares lives here, for exactly as long as the run:
the repository it runs in (its **Settings** snapshot, its worktree, working branch and stack
context) and the Gerrit service it asks. A command builds one at its entry point
(``cli_common.init_cli_runtime``); the service builds one per request. Nothing below the
entry point reaches for process-wide state to learn any of it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.rest import GerritRest
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.gerrit.stack_context import StackContext
from gerrit_workflow_tools.core.git_state import Worktree
from gerrit_workflow_tools.core.repository import Repository


@dataclass(frozen=True)
class RunContext:
    """The repository a run works in and the Gerrit it asks."""

    repo: Repository
    gerrit: GerritService

    @classmethod
    def open(
        cls,
        cwd: Path | str,
        *,
        settings: Settings | None = None,
        gerrit: GerritRest | None = None,
        refresh: bool = False,
    ) -> RunContext:
        """A run in *cwd*. Its service asks *gerrit* when given, else the configured host on first use.

        *settings* defaults to a snapshot read from *cwd*. *refresh* is ``ger --refresh``: the
        service asks Gerrit again rather than trusting what it cached.
        """
        path = Path(cwd)
        repo = Repository(path, settings if settings is not None else Settings.from_cwd(path))
        return cls(repo, GerritService(gerrit, repo=repo, refresh=refresh))

    @property
    def cwd(self) -> Path:
        return self.repo.cwd

    @property
    def settings(self) -> Settings:
        return self.repo.settings

    @property
    def worktree(self) -> Worktree:
        """Top level, checked-out branch and git dir as the run found them. Read once per run."""
        return self.repo.worktree

    @property
    def stack(self) -> StackContext:
        """The stack context of the working branch: project, target and push branch. Resolved once per run."""
        return self.repo.stack
