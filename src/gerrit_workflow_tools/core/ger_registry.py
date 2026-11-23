"""Shared two-tier loader for project extension registries (``.ger/<domain>/``).

Resolution order per domain:

1. **Project-local** — ``<gerrit.scriptsDir>/<domain>/registry.py`` (default ``.ger``), only when
   trusted (:mod:`gerrit_workflow_tools.core.registry_trust`); an untrusted one is skipped with a warning
2. **Global** — ``$XDG_CONFIG_HOME/ger/<host>/<domain>/registry.py`` (default ``~/.config/ger/``)
3. Caller falls back to built-in behavior when no tier supplies a callable for *project*

When ``registry.py`` exists but fails to import, the command fails (no silent fallback).
When a tier loads but has no entry for *project*, the next tier is tried.
When a callable raises at runtime, the next tier is tried, then built-in.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, TypeVar

from gerrit_workflow_tools.core.config import ConfigError, Settings
from gerrit_workflow_tools.core.gerrit.paths import gerrit_config_dir
from gerrit_workflow_tools.core.registry_trust import (
    local_domain_dir,
    local_registry_trusted,
    registry_source_digest,
    untrusted_hint,
)


logger = logging.getLogger(__name__)

T = TypeVar("T")

_MODULE_CACHE: dict[str, ModuleType | None] = {}
_UNTRUSTED: set[str] = set()
"""Cache keys of local registries refused this process: refused once, warned once."""

TrustGate = Callable[[str], bool]
"""Asked with a registry's :func:`registry_source_digest` before it first runs; ``False`` skips it."""

SOURCE_DIGEST = "__ger_source_digest__"
"""Global a loaded ``registry.py``, and each helper module it imports, finds its directory's
:func:`~gerrit_workflow_tools.core.registry_trust.registry_source_digest` under, taken as it loaded."""


class RegistryLoadError(ConfigError):
    """``registry.py`` exists but could not be imported."""


def global_domain_dir(web_base: str | None, domain: str) -> Path | None:
    """Return ``<configDir>/<domain>`` when ``registry.py`` exists there."""

    if not web_base or not web_base.strip():
        return None
    path = gerrit_config_dir(web_base.strip()) / domain
    return path if (path / "registry.py").is_file() else None


_DYNAMIC_PACKAGE_PREFIXES = ("ger_ci", "ger_ready", "ger_attention", "ger_reviewers")


def clear_extension_registry_cache(*, package_prefix: str | None = None) -> None:
    """Drop loaded registry modules (tests).

    When *package_prefix* is set, only modules under that name are cleared.
    """

    if package_prefix is None:
        _MODULE_CACHE.clear()
        _UNTRUSTED.clear()
        for key in list(sys.modules):
            if any(key == p or key.startswith(f"{p}.") for p in _DYNAMIC_PACKAGE_PREFIXES):
                del sys.modules[key]
        return

    for key in list(_MODULE_CACHE):
        if key.startswith(f"{package_prefix}:"):
            del _MODULE_CACHE[key]
    _UNTRUSTED.difference_update({key for key in _UNTRUSTED if key.startswith(f"{package_prefix}:")})
    for key in list(sys.modules):
        if key == package_prefix or key.startswith(f"{package_prefix}."):
            del sys.modules[key]


def _load_registry_module(domain_dir: Path, package_name: str, trust: TrustGate | None = None) -> ModuleType | None:
    """Import ``registry.py`` from *domain_dir* once per absolute path.

    *trust*, when given, decides from the code's digest whether it may run at all (the local tier).
    """

    registry = domain_dir / "registry.py"
    try:
        cache_key = f"{package_name}:{registry.resolve()}"
    except OSError:
        cache_key = f"{package_name}:{registry}"
    if cache_key in _MODULE_CACHE:
        cached = _MODULE_CACHE[cache_key]
        if cached is None:
            raise RegistryLoadError(f"failed to load strategy registry {registry}")
        return cached
    if cache_key in _UNTRUSTED:
        return None
    if not registry.is_file():
        _MODULE_CACHE[cache_key] = None
        return None

    # Taken before it runs, helpers beside it included: what describes the code this process runs.
    digest = registry_source_digest(domain_dir)
    if trust is not None and not trust(digest):
        _UNTRUSTED.add(cache_key)
        logger.warning("not running untrusted %s; %s", registry, untrusted_hint())
        return None

    pkg = sys.modules.setdefault(package_name, ModuleType(package_name))
    pkg.__path__ = [str(domain_dir)]  # type: ignore[attr-defined]

    module_name = f"{package_name}.registry"
    if module_name in sys.modules:
        del sys.modules[module_name]

    spec = importlib.util.spec_from_file_location(module_name, registry)
    if spec is None or spec.loader is None:
        _MODULE_CACHE[cache_key] = None
        raise RegistryLoadError(f"failed to load strategy registry {registry}")
    module = importlib.util.module_from_spec(spec)
    setattr(module, SOURCE_DIGEST, digest)
    already_loaded = set(sys.modules)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(module_name, None)
        _MODULE_CACHE[cache_key] = None
        raise RegistryLoadError(f"failed to load strategy registry {registry}: {exc}") from exc
    # A helper the registry imports carries the digest too: code defined there finds it in its globals.
    for name in set(sys.modules) - already_loaded:
        helper = sys.modules[name]
        if name.startswith(f"{package_name}.") and SOURCE_DIGEST not in vars(helper):
            setattr(helper, SOURCE_DIGEST, digest)
    _MODULE_CACHE[cache_key] = module
    return module


def _as_callable(value: Any) -> Callable[..., Any] | None:
    return value if callable(value) else None


def _callable_from_module(
    module: ModuleType,
    project: str,
    *,
    strategies_attr: str,
    getter_attr: str,
) -> Callable[..., Any] | None:
    getter = getattr(module, getter_attr, None)
    if callable(getter):
        try:
            found = getter(project)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.debug("%s(%r) failed: %s", getter_attr, project, exc)
            return None
        return _as_callable(found)

    strategies = getattr(module, strategies_attr, None)
    if isinstance(strategies, dict):
        return _as_callable(strategies.get(project))
    return None


def _tier_modules(
    root: Path | None, domain: str, package_name: str, settings: Settings, web_base: str | None
) -> tuple[ModuleType | None, ModuleType | None]:
    """The loaded ``(local, global)`` registry modules for *domain*; the local one only when trusted."""

    def _trusted(digest: str) -> bool:
        return local_registry_trusted(root, settings, digest)

    local_dir = local_domain_dir(root, settings.scripts_dir, domain)
    global_dir = global_domain_dir(web_base if web_base is not None else settings.gerrit_web_url, domain)
    return (
        _load_registry_module(local_dir, package_name, _trusted) if local_dir is not None else None,
        _load_registry_module(global_dir, package_name) if global_dir is not None else None,
    )


def resolve_tier_callables(
    root: Path | None,
    project: str,
    *,
    domain: str,
    package_name: str,
    settings: Settings | None = None,
    web_base: str | None = None,
    strategies_attr: str = "STRATEGIES",
    getter_attr: str = "get_strategy",
) -> tuple[Callable[..., Any] | None, Callable[..., Any] | None]:
    """Return ``(local, global)`` callables for *project* (each may be ``None``)."""

    snap = settings if settings is not None else Settings.from_cwd(root)
    local_module, global_module = _tier_modules(root, domain, package_name, snap, web_base)

    def _tier_callable(module: ModuleType | None) -> Callable[..., Any] | None:
        if module is None:
            return None
        return _callable_from_module(module, project, strategies_attr=strategies_attr, getter_attr=getter_attr)

    return _tier_callable(local_module), _tier_callable(global_module)


def resolve_registry_callable(
    root: Path | None,
    project: str,
    *,
    domain: str,
    package_name: str,
    settings: Settings | None = None,
    web_base: str | None = None,
    strategies_attr: str = "STRATEGIES",
    getter_attr: str = "get_strategy",
) -> Callable[..., Any] | None:
    """Return the first matching callable: local, then global, else ``None``."""

    local_callable, global_callable = resolve_tier_callables(
        root,
        project,
        domain=domain,
        package_name=package_name,
        settings=settings,
        web_base=web_base,
        strategies_attr=strategies_attr,
        getter_attr=getter_attr,
    )
    return local_callable if local_callable is not None else global_callable


def run_registry_callables(
    callables: tuple[Callable[..., Any] | None, Callable[..., Any] | None],
    *,
    invoke: Callable[[Callable[..., Any]], T],
    builtin: Callable[[], T],
    label: str,
    project: str,
) -> T:
    """Run *invoke* on local, then global, then *builtin* when a tier raises."""

    for tier, strategy in (("local", callables[0]), ("global", callables[1])):
        if strategy is None:
            continue
        try:
            return invoke(strategy)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(
                "%s %s strategy for %r failed: %s; trying next tier",
                label,
                tier,
                project,
                exc,
            )
    return builtin()


@dataclass(frozen=True)
class ExtensionRegistry:
    """One ``.ger/<domain>/`` registry: where its strategies load from and how they run."""

    domain: str
    package_name: str
    label: str
    strategies_attr: str = "STRATEGIES"
    getter_attr: str = "get_strategy"

    def run(
        self,
        root: Path | None,
        project: str,
        *,
        invoke: Callable[[Callable[..., Any]], T],
        builtin: Callable[[], T],
        settings: Settings | None = None,
        web_base: str | None = None,
    ) -> T:
        """*invoke* the local, then the global strategy for *project*, else *builtin*."""
        tiers = resolve_tier_callables(
            root,
            project,
            domain=self.domain,
            package_name=self.package_name,
            settings=settings,
            web_base=web_base,
            strategies_attr=self.strategies_attr,
            getter_attr=self.getter_attr,
        )
        return run_registry_callables(tiers, invoke=invoke, builtin=builtin, label=self.label, project=project)

    def tier_modules(
        self,
        root: Path | None,
        *,
        settings: Settings | None = None,
        web_base: str | None = None,
    ) -> list[ModuleType]:
        """The loaded ``registry.py`` modules, local tier first — for attributes not keyed by project."""
        snap = settings if settings is not None else Settings.from_cwd(root)
        return [m for m in _tier_modules(root, self.domain, self.package_name, snap, web_base) if m is not None]

    def clear_cache(self) -> None:
        """Drop this registry's loaded modules (tests)."""
        clear_extension_registry_cache(package_prefix=self.package_name)
