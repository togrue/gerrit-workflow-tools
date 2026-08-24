"""Load CI link strategies from ``<scriptsDir>/ci/registry.py`` (and global cache)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from gerrit_workflow_tools.core.ci_links import CiLink, CiStrategy
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.ger_registry import (
    clear_extension_registry_cache,
    local_domain_dir,
    repo_toplevel,
    resolve_registry_callable,
)

_DOMAIN = "ci"
_PACKAGE = "ger_ci"


def ger_ci_dir(cwd: Path | str | None, *, settings: Settings | None = None) -> Path | None:
    """Return ``<scriptsDir>/ci`` when that directory exists (default scriptsDir: ``.ger``)."""

    snap = settings if settings is not None else Settings.from_cwd(cwd)
    return local_domain_dir(cwd, snap.scripts_dir, _DOMAIN)


def clear_ci_strategy_cache() -> None:
    """Drop loaded CI registry modules (tests)."""

    clear_extension_registry_cache(package_prefix=_PACKAGE)


def resolve_ci_strategy(
    cwd: Path | str | None,
    project: str,
    *,
    settings: Settings | None = None,
    web_base: str | None = None,
) -> CiStrategy | None:
    """Return the extract_ci_links callable for *project*, or ``None``."""

    return resolve_registry_callable(  # type: ignore[return-value]
        cwd,
        project,
        domain=_DOMAIN,
        package_name=_PACKAGE,
        settings=settings,
        web_base=web_base,
    )


def extract_ci_links_via_registry(
    cwd: Path | str | None,
    *,
    project: str,
    checks: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    settings: Settings | None = None,
    web_base: str | None = None,
) -> list[CiLink]:
    """Load the project strategy (if any) and return filtered :class:`CiLink` rows."""

    from gerrit_workflow_tools.core.ci_links import apply_ci_strategy

    strategy = resolve_ci_strategy(cwd, project, settings=settings, web_base=web_base)
    return apply_ci_strategy(strategy, project=project, checks=checks, messages=messages)


__all__ = [
    "clear_ci_strategy_cache",
    "extract_ci_links_via_registry",
    "ger_ci_dir",
    "repo_toplevel",
    "resolve_ci_strategy",
]
