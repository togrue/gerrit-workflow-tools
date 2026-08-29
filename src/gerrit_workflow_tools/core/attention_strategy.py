"""Load attention strategies from ``<scriptsDir>/attention/registry.py``."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.ger_registry import (
    clear_extension_registry_cache,
    resolve_tier_callables,
    run_registry_callables,
)
from gerrit_workflow_tools.core.gerrit_change_status import LogCommit, determine_attention


logger = logging.getLogger(__name__)

_DOMAIN = "attention"
_PACKAGE = "ger_attention"

AttentionStrategy = Callable[..., list[str]]
ChainBlockStrategy = Callable[..., bool]


def clear_attention_strategy_cache() -> None:
    """Drop loaded attention registry modules (tests)."""

    clear_extension_registry_cache(package_prefix=_PACKAGE)


def attention_reasons_via_registry(
    cwd: Path | str | None,
    *,
    project: str,
    commit: LogCommit,
    chain_blocked: bool,
    settings: Settings | None = None,
    web_base: str | None = None,
) -> list[str]:
    """Apply local, then global, then built-in attention rules."""

    tiers = resolve_tier_callables(
        cwd,
        project,
        domain=_DOMAIN,
        package_name=_PACKAGE,
        settings=settings,
        web_base=web_base,
    )

    def _invoke(strategy: AttentionStrategy) -> list[str]:
        return list(strategy(commit=commit, chain_blocked=chain_blocked))

    def _builtin() -> list[str]:
        return determine_attention(commit, chain_blocked=chain_blocked)

    return run_registry_callables(
        tiers,
        invoke=_invoke,
        builtin=_builtin,
        label="attention",
        project=project,
    )


def commit_blocks_chain_via_registry(
    cwd: Path | str | None,
    *,
    project: str,
    commit: LogCommit,
    settings: Settings | None = None,
    web_base: str | None = None,
) -> bool:
    """Apply optional chain-block override tiers, otherwise built-in submittability rules."""

    from gerrit_workflow_tools.core.gerrit_change_status import commit_blocks_chain_for_submittability

    tiers = resolve_tier_callables(
        cwd,
        project,
        domain=_DOMAIN,
        package_name=_PACKAGE,
        settings=settings,
        web_base=web_base,
        strategies_attr="CHAIN_BLOCK_STRATEGIES",
        getter_attr="get_chain_block_strategy",
    )

    def _invoke(strategy: ChainBlockStrategy) -> bool:
        return bool(strategy(commit=commit))

    def _builtin() -> bool:
        return commit_blocks_chain_for_submittability(commit)

    return run_registry_callables(
        tiers,
        invoke=_invoke,
        builtin=_builtin,
        label="chain-block",
        project=project,
    )
