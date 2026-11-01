"""Shared two-tier loader for project extension registries (``.ger/<domain>/``).

Resolution order per domain:

1. **Project-local** — ``<gerrit.scriptsDir>/<domain>/registry.py`` (default ``.ger``)
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
from gerrit_workflow_tools.core.git_state import repo_toplevel


logger = logging.getLogger(__name__)

T = TypeVar("T")

_MODULE_CACHE: dict[str, ModuleType | None] = {}


class RegistryLoadError(ConfigError):
    """``registry.py`` exists but could not be imported."""


def resolve_scripts_root(cwd: Path | str | None, scripts_dir: str) -> Path | None:
    """Resolve ``gerrit.scriptsDir`` to an absolute path (may not exist yet)."""

    raw = scripts_dir.strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    top = repo_toplevel(cwd)
    if top is None:
        return None
    return (top / path).resolve()


def local_domain_dir(cwd: Path | str | None, scripts_dir: str, domain: str) -> Path | None:
    """Return ``<scriptsRoot>/<domain>`` when ``registry.py`` exists there."""

    root = resolve_scripts_root(cwd, scripts_dir)
    if root is None:
        return None
    path = root / domain
    return path if (path / "registry.py").is_file() else None


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
        for key in list(sys.modules):
            if any(key == p or key.startswith(f"{p}.") for p in _DYNAMIC_PACKAGE_PREFIXES):
                del sys.modules[key]
        return

    for key in list(_MODULE_CACHE):
        if key.startswith(f"{package_prefix}:"):
            del _MODULE_CACHE[key]
    for key in list(sys.modules):
        if key == package_prefix or key.startswith(f"{package_prefix}."):
            del sys.modules[key]


def _load_registry_module(domain_dir: Path, package_name: str) -> ModuleType | None:
    """Import ``registry.py`` from *domain_dir* once per absolute path."""

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
    if not registry.is_file():
        _MODULE_CACHE[cache_key] = None
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
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(module_name, None)
        _MODULE_CACHE[cache_key] = None
        raise RegistryLoadError(f"failed to load strategy registry {registry}: {exc}") from exc
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


def resolve_tier_callables(
    cwd: Path | str | None,
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

    snap = settings if settings is not None else Settings.from_cwd(cwd)
    host_base = web_base if web_base is not None else snap.gerrit_web_url

    def _tier_callable(domain_dir: Path | None) -> Callable[..., Any] | None:
        module = _load_registry_module(domain_dir, package_name) if domain_dir is not None else None
        if module is None:
            return None
        return _callable_from_module(module, project, strategies_attr=strategies_attr, getter_attr=getter_attr)

    return (
        _tier_callable(local_domain_dir(cwd, snap.scripts_dir, domain)),
        _tier_callable(global_domain_dir(host_base, domain)),
    )


def resolve_registry_callable(
    cwd: Path | str | None,
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
        cwd,
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
        cwd: Path | str | None,
        project: str,
        *,
        invoke: Callable[[Callable[..., Any]], T],
        builtin: Callable[[], T],
        settings: Settings | None = None,
        web_base: str | None = None,
    ) -> T:
        """*invoke* the local, then the global strategy for *project*, else *builtin*."""
        tiers = resolve_tier_callables(
            cwd,
            project,
            domain=self.domain,
            package_name=self.package_name,
            settings=settings,
            web_base=web_base,
            strategies_attr=self.strategies_attr,
            getter_attr=self.getter_attr,
        )
        return run_registry_callables(tiers, invoke=invoke, builtin=builtin, label=self.label, project=project)

    def clear_cache(self) -> None:
        """Drop this registry's loaded modules (tests)."""
        clear_extension_registry_cache(package_prefix=self.package_name)
