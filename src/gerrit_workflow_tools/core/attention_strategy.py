"""Load attention strategies from ``<scriptsDir>/attention/registry.py``."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from gerrit_workflow_tools.core.attention import commit_blocks_chain_for_submittability, determine_attention
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.ger_registry import ExtensionRegistry
from gerrit_workflow_tools.core.gerrit_change_status import LogCommit


_REGISTRY = ExtensionRegistry("attention", "ger_attention", label="attention")
_CHAIN_BLOCK_REGISTRY = ExtensionRegistry(
    "attention",
    "ger_attention",
    label="chain-block",
    strategies_attr="CHAIN_BLOCK_STRATEGIES",
    getter_attr="get_chain_block_strategy",
)

AttentionStrategy = Callable[..., list[str]]
ChainBlockStrategy = Callable[..., bool]


clear_attention_strategy_cache = _REGISTRY.clear_cache


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

    def _invoke(strategy: AttentionStrategy) -> list[str]:
        return list(strategy(commit=commit, chain_blocked=chain_blocked))

    def _builtin() -> list[str]:
        return determine_attention(commit, chain_blocked=chain_blocked)

    return _REGISTRY.run(cwd, project, invoke=_invoke, builtin=_builtin, settings=settings, web_base=web_base)


def commit_blocks_chain_via_registry(
    cwd: Path | str | None,
    *,
    project: str,
    commit: LogCommit,
    settings: Settings | None = None,
    web_base: str | None = None,
) -> bool:
    """Apply optional chain-block override tiers, otherwise built-in submittability rules."""

    def _invoke(strategy: ChainBlockStrategy) -> bool:
        return bool(strategy(commit=commit))

    def _builtin() -> bool:
        return commit_blocks_chain_for_submittability(commit)

    return _CHAIN_BLOCK_REGISTRY.run(
        cwd, project, invoke=_invoke, builtin=_builtin, settings=settings, web_base=web_base
    )
