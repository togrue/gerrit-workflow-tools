"""Shared two-tier loader for project extension registries (``.ger/<domain>/``).

Resolution order per domain:

1. **Project-local** — ``<gerrit.scriptsDir>/<domain>/registry.py`` (default ``.ger``)
2. **Global** — ``$XDG_CACHE_HOME/ger/<host>/<domain>/registry.py``
3. Caller falls back to built-in behavior when this returns ``None``

Local replaces global when the local registry file exists and loads successfully.
Load failures fall through to the next tier.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.paths import gerrit_cache_dir
from gerrit_workflow_tools.core.git_run import git

logger = logging.getLogger(__name__)

_MODULE_CACHE: dict[str, ModuleType | None] = {}


def repo_toplevel(cwd: Path | str | None) -> Path | None:
    """Return ``git rev-parse --show-toplevel``, or ``None`` outside a work tree."""

    p = git("rev-parse", "--show-toplevel", cwd=cwd, check=False)
    if p.returncode != 0 or not p.stdout.strip():
        return None
    return Path(p.stdout.strip())


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
    """Return ``<cacheDir>/<domain>`` when ``registry.py`` exists there."""

    if not web_base or not web_base.strip():
        return None
    path = gerrit_cache_dir(web_base.strip()) / domain
    return path if (path / "registry.py").is_file() else None


_DYNAMIC_PACKAGE_PREFIXES = ("ger_ci", "ger_ready", "ger_attention", "ger_inbox", "ger_reviewers")


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
        return _MODULE_CACHE[cache_key]
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
    # Drop a previously loaded registry for this package so a different tier can load cleanly.
    if module_name in sys.modules:
        del sys.modules[module_name]

    spec = importlib.util.spec_from_file_location(module_name, registry)
    if spec is None or spec.loader is None:
        _MODULE_CACHE[cache_key] = None
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("failed to load strategy registry %s: %s", registry, exc)
        sys.modules.pop(module_name, None)
        _MODULE_CACHE[cache_key] = None
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
    """Return the project strategy callable: local registry → global → ``None``."""

    snap = settings if settings is not None else Settings.from_cwd(cwd)
    host_base = web_base if web_base is not None else snap.gerrit_web_url

    local = local_domain_dir(cwd, snap.scripts_dir, domain)
    if local is not None:
        module = _load_registry_module(local, package_name)
        if module is not None:
            return _callable_from_module(
                module,
                project,
                strategies_attr=strategies_attr,
                getter_attr=getter_attr,
            )
        # Local dir existed but load failed — fall through to global.

    global_dir = global_domain_dir(host_base, domain)
    if global_dir is None:
        return None
    module = _load_registry_module(global_dir, package_name)
    if module is None:
        return None
    return _callable_from_module(
        module,
        project,
        strategies_attr=strategies_attr,
        getter_attr=getter_attr,
    )
