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


def _load_registry_module(domain_dir: Path, package_name: str, *, strict: bool) -> ModuleType | None:
    """Import ``registry.py`` from *domain_dir* once per absolute path."""

    registry = domain_dir / "registry.py"
    try:
        cache_key = f"{package_name}:{registry.resolve()}"
    except OSError:
        cache_key = f"{package_name}:{registry}"
    if cache_key in _MODULE_CACHE:
        cached = _MODULE_CACHE[cache_key]
        if cached is None and strict:
            raise RegistryLoadError(f"failed to load strategy registry {registry}")
        return cached
    if not registry.is_file():
        _MODULE_CACHE[cache_key] = None
        return None

    if package_name not in sys.modules:
        pkg = ModuleType(package_name)
        pkg.__path__ = [str(domain_dir)]  # type: ignore[attr-defined]
        sys.modules[package_name] = pkg
    else:
        pkg = sys.modules[package_name]
        pkg.__path__ = [str(domain_dir)]  # type: ignore[attr-defined]

    module_name = f"{package_name}.registry"
    if module_name in sys.modules:
        del sys.modules[module_name]

    spec = importlib.util.spec_from_file_location(module_name, registry)
    if spec is None or spec.loader is None:
        _MODULE_CACHE[cache_key] = None
        if strict:
            raise RegistryLoadError(f"failed to load strategy registry {registry}")
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(module_name, None)
        _MODULE_CACHE[cache_key] = None
        if strict:
            raise RegistryLoadError(f"failed to load strategy registry {registry}: {exc}") from exc
        logger.warning("failed to load strategy registry %s: %s", registry, exc)
        return None
    _MODULE_CACHE[cache_key] = module
    return module


def _as_callable(value: Any) -> Callable[..., Any] | None:
    if callable(value):
        return value
    return None


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

    local_callable: Callable[..., Any] | None = None
    local_dir = local_domain_dir(cwd, snap.scripts_dir, domain)
    if local_dir is not None:
        module = _load_registry_module(local_dir, package_name, strict=True)
        if module is not None:
            local_callable = _callable_from_module(
                module,
                project,
                strategies_attr=strategies_attr,
                getter_attr=getter_attr,
            )

    global_callable: Callable[..., Any] | None = None
    global_dir = global_domain_dir(host_base, domain)
    if global_dir is not None:
        module = _load_registry_module(global_dir, package_name, strict=True)
        if module is not None:
            global_callable = _callable_from_module(
                module,
                project,
                strategies_attr=strategies_attr,
                getter_attr=getter_attr,
            )

    return local_callable, global_callable


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
    if local_callable is not None:
        return local_callable
    return global_callable


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
