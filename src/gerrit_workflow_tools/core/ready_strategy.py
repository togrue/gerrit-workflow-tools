"""Load ready-boundary strategies from ``<scriptsDir>/ready/registry.py``."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.ger_registry import ExtensionRegistry
from gerrit_workflow_tools.core.stack import Commit


if TYPE_CHECKING:
    from gerrit_workflow_tools.core.gerrit_change_status import LogCommit


_REGISTRY = ExtensionRegistry("ready", "ger_ready", label="ready boundary")

clear_ready_strategy_cache = _REGISTRY.clear_cache


@dataclass(frozen=True)
class BoundaryResult:
    """Output of a ready-boundary strategy."""

    block_index: int | None
    """Index into the commits list of the first blocking commit, or ``None``."""

    reason: str
    """Human-readable boundary reason for push preview and debug output."""


ReadyBoundaryStrategy = Callable[..., BoundaryResult]


def default_find_ready_boundary(
    *,
    commits: list[Commit],
    stop_pattern: str,
    overlay: dict[str, LogCommit] | None = None,
) -> BoundaryResult:
    """Built-in stop-pattern boundary: first subject match blocks the stack tail."""

    del overlay
    if not stop_pattern:
        return BoundaryResult(block_index=None, reason="no stop pattern matched")
    for index, row in enumerate(commits):
        try:
            if re.search(stop_pattern, row.subject, re.IGNORECASE):
                return BoundaryResult(
                    block_index=index,
                    reason=f"subject matches stop pattern {stop_pattern!r}",
                )
        except re.error:
            continue
    return BoundaryResult(block_index=None, reason="no stop pattern matched")


def blocked_shas_for_stack(
    cwd: Path | str | None,
    *,
    project: str,
    commits: list[Commit],
    stop_pattern: str,
    settings: Settings | None = None,
    web_base: str | None = None,
    overlay: dict[str, LogCommit] | None = None,
) -> frozenset[str]:
    """Return SHAs at or after the ready boundary (non-pushable stack tail)."""

    # Imported lazily to avoid a ready_strategy ↔ ready_calc import cycle at module load.
    from gerrit_workflow_tools.core.ready_calc import compose_ready_boundary

    strategy = find_ready_boundary_via_registry(
        cwd,
        project=project,
        commits=commits,
        stop_pattern=stop_pattern,
        overlay=overlay,
        settings=settings,
        web_base=web_base,
    )
    boundary = compose_ready_boundary(strategy, commits)
    if boundary.block_index is None:
        return frozenset()
    return frozenset(row.sha for row in commits[boundary.block_index :])


def find_ready_boundary_via_registry(
    cwd: Path | str | None,
    *,
    project: str,
    commits: list[Commit],
    stop_pattern: str,
    overlay: dict[str, LogCommit] | None = None,
    settings: Settings | None = None,
    web_base: str | None = None,
) -> BoundaryResult:
    """Apply local, then global, then built-in ready-boundary rules."""

    def _invoke(strategy: ReadyBoundaryStrategy) -> BoundaryResult:
        return strategy(commits=commits, stop_pattern=stop_pattern, overlay=overlay)

    def _builtin() -> BoundaryResult:
        return default_find_ready_boundary(commits=commits, stop_pattern=stop_pattern, overlay=overlay)

    return _REGISTRY.run(cwd, project, invoke=_invoke, builtin=_builtin, settings=settings, web_base=web_base)
