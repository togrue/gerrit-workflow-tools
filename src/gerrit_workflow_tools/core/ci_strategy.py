"""Load repo-local CI link strategies from ``.ger/ci/registry.py``."""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from gerrit_workflow_tools.core.ci_links import CiLink, CiStrategy
from gerrit_workflow_tools.core.git_run import git

logger = logging.getLogger(__name__)

_CACHE: dict[str, ModuleType | None] = {}


def repo_toplevel(cwd: Path | str | None) -> Path | None:
    """Return ``git rev-parse --show-toplevel``, or ``None`` outside a work tree."""

    p = git("rev-parse", "--show-toplevel", cwd=cwd, check=False)
    if p.returncode != 0 or not p.stdout.strip():
        return None
    return Path(p.stdout.strip())


def ger_ci_dir(cwd: Path | str | None) -> Path | None:
    """Return ``<toplevel>/.ger/ci`` when that directory exists."""

    top = repo_toplevel(cwd)
    if top is None:
        return None
    path = top / ".ger" / "ci"
    return path if path.is_dir() else None


def _load_registry_module(ci_dir: Path) -> ModuleType | None:
    """Import ``.ger/ci/registry.py`` once per absolute path."""

    registry = ci_dir / "registry.py"
    cache_key = str(registry.resolve())
    if cache_key in _CACHE:
        return _CACHE[cache_key]
    if not registry.is_file():
        _CACHE[cache_key] = None
        return None

    # Ensure sibling modules under ``.ger/ci/`` are importable as package children.
    package_name = "ger_ci"
    if package_name not in sys.modules:
        pkg = ModuleType(package_name)
        pkg.__path__ = [str(ci_dir)]  # type: ignore[attr-defined]
        sys.modules[package_name] = pkg

    spec = importlib.util.spec_from_file_location(f"{package_name}.registry", registry)
    if spec is None or spec.loader is None:
        _CACHE[cache_key] = None
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("failed to load CI strategy registry %s: %s", registry, exc)
        _CACHE[cache_key] = None
        return None
    _CACHE[cache_key] = module
    return module


def clear_ci_strategy_cache() -> None:
    """Drop loaded registry modules (tests)."""

    _CACHE.clear()
    for key in list(sys.modules):
        if key == "ger_ci" or key.startswith("ger_ci."):
            del sys.modules[key]


def _as_strategy(value: Any) -> CiStrategy | None:
    if callable(value):
        return value  # type: ignore[return-value]
    return None


def resolve_ci_strategy(cwd: Path | str | None, project: str) -> CiStrategy | None:
    """Return the extract_ci_links callable for *project*, or ``None``."""

    ci_dir = ger_ci_dir(cwd)
    if ci_dir is None:
        return None
    module = _load_registry_module(ci_dir)
    if module is None:
        return None

    getter = getattr(module, "get_strategy", None)
    if callable(getter):
        try:
            found = getter(project)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.debug("get_strategy(%r) failed: %s", project, exc)
            return None
        return _as_strategy(found)

    strategies = getattr(module, "STRATEGIES", None)
    if isinstance(strategies, dict):
        return _as_strategy(strategies.get(project))

    return None


def extract_ci_links_via_registry(
    cwd: Path | str | None,
    *,
    project: str,
    checks: list[dict[str, Any]],
    messages: list[dict[str, Any]],
) -> list[CiLink]:
    """Load the project strategy (if any) and return filtered :class:`CiLink` rows."""

    from gerrit_workflow_tools.core.ci_links import apply_ci_strategy

    strategy = resolve_ci_strategy(cwd, project)
    return apply_ci_strategy(strategy, project=project, checks=checks, messages=messages)
