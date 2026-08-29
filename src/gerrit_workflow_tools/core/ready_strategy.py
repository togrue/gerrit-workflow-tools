"""Load ready-boundary strategies from ``<scriptsDir>/ready/registry.py``."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.ger_registry import (
    clear_extension_registry_cache,
    resolve_tier_callables,
    run_registry_callables,
)


if TYPE_CHECKING:
    from gerrit_workflow_tools.core.gerrit_change_status import LogCommit

logger = logging.getLogger(__name__)

_DOMAIN = "ready"
_PACKAGE = "ger_ready"


@dataclass(frozen=True)
class ReadyCommitRow:
    """One local stack commit passed to a ready-boundary strategy."""

    sha: str
    short_sha: str
    subject: str
    change_id: str | None


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
    commits: list[ReadyCommitRow],
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
    commits: list[ReadyCommitRow],
    stop_pattern: str,
    settings: Settings | None = None,
    web_base: str | None = None,
    overlay: dict[str, LogCommit] | None = None,
) -> frozenset[str]:
    """Return SHAs at or after the ready boundary (non-pushable stack tail)."""

    boundary = find_ready_boundary_via_registry(
        cwd,
        project=project,
        commits=commits,
        stop_pattern=stop_pattern,
        overlay=overlay,
        settings=settings,
        web_base=web_base,
    )
    if boundary.block_index is None:
        return frozenset()
    return frozenset(row.sha for row in commits[boundary.block_index :])


def clear_ready_strategy_cache() -> None:
    """Drop loaded ready registry modules (tests)."""

    clear_extension_registry_cache(package_prefix=_PACKAGE)


def find_ready_boundary_via_registry(
    cwd: Path | str | None,
    *,
    project: str,
    commits: list[ReadyCommitRow],
    stop_pattern: str,
    overlay: dict[str, LogCommit] | None = None,
    settings: Settings | None = None,
    web_base: str | None = None,
) -> BoundaryResult:
    """Apply local, then global, then built-in ready-boundary rules."""

    tiers = resolve_tier_callables(
        cwd,
        project,
        domain=_DOMAIN,
        package_name=_PACKAGE,
        settings=settings,
        web_base=web_base,
    )

    def _invoke(strategy: ReadyBoundaryStrategy) -> BoundaryResult:
        return strategy(commits=commits, stop_pattern=stop_pattern, overlay=overlay)

    def _builtin() -> BoundaryResult:
        return default_find_ready_boundary(commits=commits, stop_pattern=stop_pattern, overlay=overlay)

    return run_registry_callables(
        tiers,
        invoke=_invoke,
        builtin=_builtin,
        label="ready boundary",
        project=project,
    )
