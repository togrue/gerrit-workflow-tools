"""Load default-reviewer strategies from ``<scriptsDir>/reviewers/registry.py``."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.ger_registry import ExtensionRegistry
from gerrit_workflow_tools.core.stack import Commit


_REGISTRY = ExtensionRegistry("reviewers", "ger_reviewers", label="reviewer defaults")

ReviewerDefaultsStrategy = Callable[..., list[str]]


clear_reviewers_strategy_cache = _REGISTRY.clear_cache


def default_reviewers_via_registry(
    cwd: Path | str | None,
    *,
    project: str,
    branch: str,
    commits: list[Commit],
    settings: Settings,
    web_base: str | None = None,
) -> list[str] | None:
    """Return registry-provided reviewers, or ``None`` to fall back to branch config."""

    def _invoke(strategy: ReviewerDefaultsStrategy) -> list[str] | None:
        raw = strategy(branch=branch, commits=commits, settings=settings)
        return [str(name).strip() for name in raw if str(name).strip()]

    return _REGISTRY.run(cwd, project, invoke=_invoke, builtin=lambda: None, settings=settings, web_base=web_base)
