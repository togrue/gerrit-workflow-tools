"""Load default-reviewer strategies from ``<scriptsDir>/reviewers/registry.py``."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.ger_registry import clear_extension_registry_cache, resolve_tier_callables
from gerrit_workflow_tools.core.ready_strategy import ReadyCommitRow


logger = logging.getLogger(__name__)

_DOMAIN = "reviewers"
_PACKAGE = "ger_reviewers"

ReviewerDefaultsStrategy = Callable[..., list[str]]


def clear_reviewers_strategy_cache() -> None:
    """Drop loaded reviewers registry modules (tests)."""

    clear_extension_registry_cache(package_prefix=_PACKAGE)


def default_reviewers_via_registry(
    cwd: Path | str | None,
    *,
    project: str,
    branch: str,
    commits: list[ReadyCommitRow],
    settings: Settings,
    web_base: str | None = None,
) -> list[str] | None:
    """Return registry-provided reviewers, or ``None`` to fall back to branch config."""

    tiers = resolve_tier_callables(
        cwd,
        project,
        domain=_DOMAIN,
        package_name=_PACKAGE,
        settings=settings,
        web_base=web_base,
    )
    if tiers[0] is None and tiers[1] is None:
        return None

    for tier, strategy in (("local", tiers[0]), ("global", tiers[1])):
        if strategy is None:
            continue
        try:
            raw = strategy(branch=branch, commits=commits, settings=settings)
            return [str(name).strip() for name in raw if str(name).strip()]
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(
                "reviewer defaults %s strategy for %r failed: %s; trying next tier",
                tier,
                project,
                exc,
            )
    return None
