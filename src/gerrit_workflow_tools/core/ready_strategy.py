"""Load ready-boundary strategies from ``<scriptsDir>/ready/registry.py``."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from gerrit_workflow_tools.core.change_id import validate_change_id_value
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.fixups import is_unsquashed_fixup
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
                    reason=f"subject matches stop pattern `{stop_pattern}`",
                )
        except re.error:
            continue
    return BoundaryResult(block_index=None, reason="no stop pattern matched")


def blocked_shas_for_stack(
    root: Path | None,
    *,
    project: str,
    commits: list[Commit],
    stop_pattern: str,
    settings: Settings | None = None,
    web_base: str | None = None,
    overlay: dict[str, LogCommit] | None = None,
    change_ids_added: bool = False,
) -> frozenset[str]:
    """Return SHAs at or after the ready boundary (non-pushable stack tail).

    *change_ids_added*: ``ger push`` gives the commits without a Change-Id one before it pushes
    (:func:`change_ids_push_adds`), so no Change-Id error holds them back: the boundary is the
    strategy's or the stop pattern's alone, as the push finds it once they have one.
    """

    # Imported lazily to avoid a ready_strategy ↔ ready_calc import cycle at module load.
    from gerrit_workflow_tools.core.ready_calc import compose_ready_boundary

    strategy = find_ready_boundary_via_registry(
        root,
        project=project,
        commits=commits,
        stop_pattern=stop_pattern,
        overlay=overlay,
        settings=settings,
        web_base=web_base,
    )
    boundary = strategy if change_ids_added else compose_ready_boundary(strategy, commits)
    if boundary.block_index is None:
        return frozenset()
    return frozenset(row.sha for row in commits[boundary.block_index :])


def lacks_change_id(footer: str | None, subject: str) -> bool:
    """A commit ``ger push`` adds a Change-Id to: no footer at all (not a malformed one), and not a ``fixup!``
    commit, which is squashed before it is pushed."""
    return validate_change_id_value(footer) == (False, False) and not is_unsquashed_fixup(subject)


def change_ids_push_adds(commits: list[Commit]) -> frozenset[str]:
    """The commits ``ger push`` gives a Change-Id before its preview, on the branch checked out.

    It does where the first Change-Id error of the stack (oldest first) is a commit without any
    Change-Id footer (``add_change_ids_for_push``), and then it fixes every one: each commit
    without a valid Change-Id, and the newer of two that carry the same one. A malformed footer
    or an unsquashed ``fixup!`` first holds the push back instead.
    """
    from gerrit_workflow_tools.core.ready_calc import change_id_boundary_for_commits

    first = change_id_boundary_for_commits(commits).block_index
    if first is None or not lacks_change_id(commits[first].change_id, commits[first].subject):
        return frozenset()
    seen: set[str] = set()
    adds: set[str] = set()
    for commit in commits:
        valid = (commit.valid_change_id or "").lower()
        if not valid or valid in seen:
            adds.add(commit.sha)
        seen.add(valid)
    return frozenset(adds)


def find_ready_boundary_via_registry(
    root: Path | None,
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

    return _REGISTRY.run(root, project, invoke=_invoke, builtin=_builtin, settings=settings, web_base=web_base)
