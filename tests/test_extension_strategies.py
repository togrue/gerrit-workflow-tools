"""Tests for attention and reviewers extension strategies."""

from __future__ import annotations

from pathlib import Path

from gerrit_workflow_tools.core.attention_strategy import (
    attention_reasons_via_registry,
    clear_attention_strategy_cache,
    commit_blocks_chain_via_registry,
)
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.ger_registry import clear_extension_registry_cache
from gerrit_workflow_tools.core.gerrit_change_status import LogCommit, PatchsetStatus, annotate_attention
from gerrit_workflow_tools.core.ready_strategy import ReadyCommitRow
from gerrit_workflow_tools.core.reviewers_strategy import clear_reviewers_strategy_cache, default_reviewers_via_registry


def _write_registry(repo: Path, domain: str, body: str) -> None:
    d = repo / ".ger" / domain
    d.mkdir(parents=True, exist_ok=True)
    (d / "registry.py").write_text(body, encoding="utf-8")
    clear_extension_registry_cache()


def _commit(**kwargs: object) -> LogCommit:
    defaults: dict[str, object] = {
        "sha": "a" * 40,
        "short_sha": "aaaaaaaa",
        "summary": "x",
        "change_id": "I" + "b" * 40,
        "pushed": True,
        "abandoned": False,
        "patchset_status": PatchsetStatus.ACTIVE,
        "verified": 1,
        "code_review": 2,
        "comments_unresolved": 0,
        "submittable": True,
        "reviewers": [],
    }
    defaults.update(kwargs)
    return LogCommit(**defaults)  # type: ignore[arg-type]


def test_attention_strategy_override(stack_repo: Path) -> None:
    _write_registry(
        stack_repo,
        "attention",
        """
def _attn(*, commit, chain_blocked):
    return ["custom-attention"]
STRATEGIES = {"testproj": _attn}
""",
    )
    clear_attention_strategy_cache()
    commit = _commit()
    reasons = attention_reasons_via_registry(
        stack_repo,
        project="testproj",
        commit=commit,
        chain_blocked=False,
        settings=Settings.from_map({}),
    )
    assert reasons == ["custom-attention"]


def test_chain_block_strategy_override(stack_repo: Path) -> None:
    _write_registry(
        stack_repo,
        "attention",
        """
def _block(*, commit):
    return False
CHAIN_BLOCK_STRATEGIES = {"testproj": _block}
""",
    )
    clear_attention_strategy_cache()
    commit = _commit(submittable=False)
    assert (
        commit_blocks_chain_via_registry(
            stack_repo,
            project="testproj",
            commit=commit,
            settings=Settings.from_map({}),
        )
        is False
    )


def test_annotate_attention_uses_registry(stack_repo: Path) -> None:
    _write_registry(
        stack_repo,
        "attention",
        """
def _attn(*, commit, chain_blocked):
    return ["from-annotate"]
STRATEGIES = {"p": _attn}
""",
    )
    clear_attention_strategy_cache()
    commit = _commit()
    annotate_attention([commit], cwd=stack_repo, project="p", settings=Settings.from_map({}))
    assert commit.attention_reasons == ["from-annotate"]


def test_reviewers_strategy(stack_repo: Path) -> None:
    _write_registry(
        stack_repo,
        "reviewers",
        """
def _revs(*, branch, commits, settings):
    return ["alice", "bob"]
STRATEGIES = {"testproj": _revs}
""",
    )
    clear_reviewers_strategy_cache()
    out = default_reviewers_via_registry(
        stack_repo,
        project="testproj",
        branch="feature",
        commits=[ReadyCommitRow(sha="a" * 40, short_sha="aaaaaaaa", subject="x", change_id=None)],
        settings=Settings.from_map({}),
    )
    assert out == ["alice", "bob"]
