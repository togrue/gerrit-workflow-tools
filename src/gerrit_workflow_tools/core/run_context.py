"""The **run context**: one command run, or one request ``ger serve`` answers.

Everything a run reads once and then shares lives here, for exactly as long as the run:
the repository it runs in, its **Settings** snapshot, and the Gerrit service it asks.
A command builds one at its entry point (``cli_common.init_cli_runtime``); the service
builds one per request. Nothing below the entry point reaches for process-wide state to
learn any of it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.rest import GerritRest
from gerrit_workflow_tools.core.gerrit.service import GerritService


@dataclass(frozen=True)
class RunContext:
    """Where a run works, the settings it reads, and the Gerrit it asks."""

    cwd: Path
    settings: Settings
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
        snapshot = settings if settings is not None else Settings.from_cwd(path)
        return cls(path, snapshot, GerritService(gerrit, cwd=path, settings=snapshot, refresh=refresh))
