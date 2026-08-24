"""Load attention strategies from ``<scriptsDir>/attention/registry.py``."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.ger_registry import clear_extension_registry_cache, resolve_registry_callable
from gerrit_workflow_tools.core.gerrit_change_status import LogCommit, determine_attention

logger = logging.getLogger(__name__)

_DOMAIN = "attention"
_PACKAGE = "ger_attention"

AttentionStrategy = Callable[..., list[str]]
ChainBlockStrategy = Callable[..., bool]


def clear_attention_strategy_cache() -> None:
    """Drop loaded attention registry modules (tests)."""

    clear_extension_registry_cache(package_prefix=_PACKAGE)


def resolve_attention_strategy(
    cwd: Path | str | None,
    project: str,
    *,
    settings: Settings | None = None,
    web_base: str | None = None,
) -> AttentionStrategy | None:
    """Return ``attention_reasons`` for *project*, or ``None``."""

    return resolve_registry_callable(  # type: ignore[return-value]
        cwd,
        project,
        domain=_DOMAIN,
        package_name=_PACKAGE,
        settings=settings,
        web_base=web_base,
    )


def resolve_chain_block_strategy(
    cwd: Path | str | None,
    project: str,
    *,
    settings: Settings | None = None,
    web_base: str | None = None,
) -> ChainBlockStrategy | None:
    """Return ``commit_blocks_chain`` for *project*, or ``None``."""

    return resolve_registry_callable(  # type: ignore[return-value]
        cwd,
        project,
        domain=_DOMAIN,
        package_name=_PACKAGE,
        settings=settings,
        web_base=web_base,
        strategies_attr="CHAIN_BLOCK_STRATEGIES",
        getter_attr="get_chain_block_strategy",
    )


def attention_reasons_via_registry(
    cwd: Path | str | None,
    *,
    project: str,
    commit: LogCommit,
    chain_blocked: bool,
    settings: Settings | None = None,
    web_base: str | None = None,
) -> list[str]:
    """Apply the project strategy when present, otherwise built-in attention rules."""

    strategy = resolve_attention_strategy(cwd, project, settings=settings, web_base=web_base)
    if strategy is None:
        return determine_attention(commit, chain_blocked=chain_blocked)
    try:
        return list(strategy(commit=commit, chain_blocked=chain_blocked))
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("attention strategy for %r failed: %s; using default", project, exc)
        return determine_attention(commit, chain_blocked=chain_blocked)


def commit_blocks_chain_via_registry(
    cwd: Path | str | None,
    *,
    project: str,
    commit: LogCommit,
    settings: Settings | None = None,
    web_base: str | None = None,
) -> bool:
    """Apply optional chain-block override, otherwise built-in submittability rules."""

    from gerrit_workflow_tools.core.gerrit_change_status import commit_blocks_chain_for_submittability

    strategy = resolve_chain_block_strategy(cwd, project, settings=settings, web_base=web_base)
    if strategy is None:
        return commit_blocks_chain_for_submittability(commit)
    try:
        return bool(strategy(commit=commit))
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("chain-block strategy for %r failed: %s; using default", project, exc)
        return commit_blocks_chain_for_submittability(commit)
