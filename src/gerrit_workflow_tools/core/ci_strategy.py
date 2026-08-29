"""Load CI link strategies from ``<scriptsDir>/ci/registry.py`` (and global config dir)."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from gerrit_workflow_tools.core.ci_links import CiLink, CiStrategy
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit_message_parsing import builtin_extract_ci_links
from gerrit_workflow_tools.core.ger_registry import (
    clear_extension_registry_cache,
    local_domain_dir,
    resolve_tier_callables,
    run_registry_callables,
)

_DOMAIN = "ci"
_PACKAGE = "ger_ci"


class LazyRows(Sequence[Mapping[str, Any]]):
    """Row list that fetches on first use and caches the result.

    Iterating, indexing, or ``len()`` triggers *fetch* at most once. A strategy
    that never reads these rows never pays for the REST call.
    """

    def __init__(self, fetch: Callable[[], list[Mapping[str, Any]]]) -> None:
        self._fetch = fetch
        self._rows: list[Mapping[str, Any]] | None = None

    def _materialized(self) -> list[Mapping[str, Any]]:
        if self._rows is None:
            self._rows = list(self._fetch())
        return self._rows

    def __iter__(self):
        return iter(self._materialized())

    def __len__(self) -> int:
        return len(self._materialized())

    def __getitem__(self, index: int | slice) -> Mapping[str, Any] | Sequence[Mapping[str, Any]]:
        return self._materialized()[index]


def ger_ci_dir(cwd: Path | str | None, *, settings: Settings | None = None) -> Path | None:
    """Return ``<scriptsDir>/ci`` when ``registry.py`` exists there (default scriptsDir: ``.ger``)."""

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
    """Return the first matching ``extract_ci_links`` callable, or ``None``."""

    local_callable, global_callable = resolve_tier_callables(
        cwd,
        project,
        domain=_DOMAIN,
        package_name=_PACKAGE,
        settings=settings,
        web_base=web_base,
    )
    if local_callable is not None:
        return local_callable  # type: ignore[return-value]
    return global_callable  # type: ignore[return-value]


def extract_ci_links_via_registry(
    cwd: Path | str | None,
    *,
    project: str,
    checks: list[dict[str, Any]],
    messages: Sequence[Mapping[str, Any]],
    settings: Settings | None = None,
    web_base: str | None = None,
) -> list[CiLink]:
    """Load tiered CI strategies and return filtered :class:`CiLink` rows."""

    from gerrit_workflow_tools.core.ci_links import apply_ci_strategy

    tiers = resolve_tier_callables(
        cwd,
        project,
        domain=_DOMAIN,
        package_name=_PACKAGE,
        settings=settings,
        web_base=web_base,
    )

    def _invoke(strategy: CiStrategy) -> list[CiLink]:
        return apply_ci_strategy(strategy, project=project, checks=checks, messages=messages)

    def _builtin() -> list[CiLink]:
        return apply_ci_strategy(
            builtin_extract_ci_links,
            project=project,
            checks=checks,
            messages=messages,
        )

    return run_registry_callables(
        tiers,
        invoke=_invoke,
        builtin=_builtin,
        label="CI links",
        project=project,
    )


__all__ = [
    "LazyRows",
    "clear_ci_strategy_cache",
    "extract_ci_links_via_registry",
    "ger_ci_dir",
    "resolve_ci_strategy",
]
