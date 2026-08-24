"""Load inbox query strategies from ``<scriptsDir>/inbox/registry.py``."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.ger_registry import clear_extension_registry_cache, resolve_registry_callable

logger = logging.getLogger(__name__)

_DOMAIN = "inbox"
_PACKAGE = "ger_inbox"

InboxQueryStrategy = Callable[..., str]


def clear_inbox_strategy_cache() -> None:
    """Drop loaded inbox registry modules (tests)."""

    clear_extension_registry_cache(package_prefix=_PACKAGE)


def resolve_inbox_query_strategy(
    cwd: Path | str | None,
    project: str,
    *,
    settings: Settings | None = None,
    web_base: str | None = None,
) -> InboxQueryStrategy | None:
    """Return ``build_to_review_query`` for *project*, or ``None``."""

    return resolve_registry_callable(  # type: ignore[return-value]
        cwd,
        project,
        domain=_DOMAIN,
        package_name=_PACKAGE,
        settings=settings,
        web_base=web_base,
    )


def build_to_review_query_via_registry(
    cwd: Path | str | None,
    *,
    project: str,
    settings: Settings,
    projects: list[str],
    include_unready: bool,
    default_query: str,
    web_base: str | None = None,
) -> str | None:
    """Return a registry-built query, or ``None`` to use the built-in assembler.

    On strategy failure, returns *default_query* so callers can treat the result as final.
    """

    strategy = resolve_inbox_query_strategy(cwd, project, settings=settings, web_base=web_base)
    if strategy is None:
        return None
    try:
        return str(
            strategy(
                settings=settings,
                projects=projects,
                include_unready=include_unready,
            )
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("inbox query strategy for %r failed: %s; using default", project, exc)
        return default_query
