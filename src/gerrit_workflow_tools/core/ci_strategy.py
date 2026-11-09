"""Load CI link strategies and **CI content providers** from ``<scriptsDir>/ci/registry.py`` (and global config dir)."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, overload

from gerrit_workflow_tools.core.ci_links import CiLink, CiStrategy
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.ger_registry import ExtensionRegistry
from gerrit_workflow_tools.core.gerrit_message_parsing import (
    ci_links_from_build_failed_messages,
    ci_links_from_failed_checks,
)


if TYPE_CHECKING:
    from gerrit_workflow_tools.core.ci_content import CiContentProvider


logger = logging.getLogger(__name__)

_REGISTRY = ExtensionRegistry("ci", "ger_ci", label="CI links")

clear_ci_strategy_cache = _REGISTRY.clear_cache


class LazyRows(Sequence[Mapping[str, Any]]):
    """Row list that fetches on first use and caches the result.

    Iterating, indexing, or ``len()`` triggers *fetch* at most once. A strategy
    that never reads these rows never pays for the REST call.
    """

    def __init__(self, fetch: Callable[[], Sequence[Mapping[str, Any]]]) -> None:
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

    @overload
    def __getitem__(self, index: int) -> Mapping[str, Any]: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[Mapping[str, Any]]: ...

    def __getitem__(self, index: int | slice) -> Mapping[str, Any] | Sequence[Mapping[str, Any]]:
        return self._materialized()[index]


def default_extract_ci_links(
    *,
    project: str,
    checks: Sequence[Mapping[str, Any]],
    messages: Sequence[Mapping[str, Any]],
    current_revision_number: int | None = None,
) -> list[CiLink]:
    """Built-in CI strategy: failed Checks rows, then Jenkins ``Build Failed`` messages."""

    del project  # built-in applies to every project
    from_checks = ci_links_from_failed_checks(checks)
    if from_checks:
        # LazyRows gate: do not iterate messages (and trigger get_messages) when checks answer.
        return from_checks
    return ci_links_from_build_failed_messages(
        messages,
        current_revision_number=current_revision_number,
    )


def extract_ci_links_via_registry(
    cwd: Path | str | None,
    *,
    project: str,
    checks: list[dict[str, Any]],
    messages: Sequence[Mapping[str, Any]],
    settings: Settings | None = None,
    web_base: str | None = None,
    current_revision_number: int | None = None,
) -> list[CiLink]:
    """Load tiered CI strategies and return filtered :class:`CiLink` rows."""

    from gerrit_workflow_tools.core.ci_links import apply_ci_strategy, prefer_checks_links

    def _invoke(strategy: CiStrategy) -> list[CiLink]:
        return apply_ci_strategy(strategy, project=project, checks=checks, messages=messages)

    def _builtin() -> list[CiLink]:
        raw = default_extract_ci_links(
            project=project,
            checks=checks,
            messages=messages,
            current_revision_number=current_revision_number,
        )
        return prefer_checks_links(raw)

    return _REGISTRY.run(cwd, project, invoke=_invoke, builtin=_builtin, settings=settings, web_base=web_base)


def load_ci_content_providers(
    cwd: Path | str | None,
    *,
    settings: Settings | None = None,
    web_base: str | None = None,
) -> list[CiContentProvider]:
    """**CI content providers** in match order: ``CONTENT_PROVIDERS`` of the local, then the
    global ``.ger/ci/registry.py``, then the built-in Jenkins provider.

    Providers are matched by URL host/pattern, not by ``gerrit.project``, so every tier's
    list is used. Entries without ``matches`` / ``fetch`` are skipped with a warning.
    """
    from gerrit_workflow_tools.core.ci_jenkins import JenkinsProvider

    providers: list[CiContentProvider] = []
    for module in _REGISTRY.tier_modules(cwd, settings=settings, web_base=web_base):
        for candidate in getattr(module, "CONTENT_PROVIDERS", None) or ():
            if all(callable(getattr(candidate, attr, None)) for attr in ("matches", "fetch")):
                providers.append(candidate)
            else:
                logger.warning("ignoring CI content provider %r: it needs matches() and fetch()", candidate)
    providers.append(JenkinsProvider())
    return providers


__all__ = [
    "LazyRows",
    "clear_ci_strategy_cache",
    "default_extract_ci_links",
    "extract_ci_links_via_registry",
    "load_ci_content_providers",
]
