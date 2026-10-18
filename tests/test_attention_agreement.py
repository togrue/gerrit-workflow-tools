"""Every reader of attention agrees with the one verdict in ``core.attention``.

The exit code, ``ger edit --first-attention-commit``, the ``ger log`` hints, the rebase todo
and the inbox once each decided "build failed" themselves, and drifted (``== -1`` vs
``<= -1`` vs a failed Checks run). These tests pin them to one answer.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from gerrit_workflow_tools.core.attention import Attention, annotate_attention, commit_needs_edit_attention
from gerrit_workflow_tools.core.attention_strategy import clear_attention_strategy_cache
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.ger_registry import clear_extension_registry_cache
from gerrit_workflow_tools.core.gerrit.change_view import ChangeView
from gerrit_workflow_tools.core.gerrit_change_status import LogCommit, PatchsetStatus
from gerrit_workflow_tools.core.review_chain import member_attention_reasons
from gerrit_workflow_tools.core.reviewer import ReviewerAccount
from gerrit_workflow_tools.render.commit_row import attention_tokens
from gerrit_workflow_tools.render.status_fmt import attention_text


_STABLE = LogCommit(
    sha="a" * 40,
    short_sha="aaaaaaaa",
    summary="subj",
    change_id="I" + "b" * 40,
    pushed=True,
    abandoned=False,
    patchset_status=PatchsetStatus.ACTIVE,
    verified=1,
    code_review=2,
    comments_unresolved=0,
    submittable=True,
    reviewers=[ReviewerAccount(slug="alice")],
)


def _annotated(**kwargs: Any) -> LogCommit:
    commit = replace(_STABLE, **kwargs)
    annotate_attention([commit])
    return commit


def _texts(commit: LogCommit) -> list[str]:
    return [text for text, _code in attention_tokens(commit)]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"verified": -1},
        {"verified": -2},
        {"verified": 0, "ci_failures": ["unit-tests"]},
        {"verified": None, "ci_failures": ["unit-tests"]},
    ],
    ids=["verified-1", "verified-2", "checks-only", "checks-no-vote"],
)
def test_a_failed_build_is_one_verdict_everywhere(kwargs: dict[str, Any]) -> None:
    commit = _annotated(**kwargs)

    assert Attention.CI_FAILED in commit.attention_reasons
    assert commit_needs_edit_attention(commit)
    assert "build failed" in _texts(commit)
    assert attention_text(commit).startswith(("build failed", "CI failed:"))


def test_inbox_member_uses_the_same_ci_rule() -> None:
    payload: dict[str, Any] = {"labels": {"Verified": {"value": -2, "all": [{"value": -2}]}}}
    assert Attention.CI_FAILED in member_attention_reasons(ChangeView.from_payload(payload))


def test_a_stable_commit_prints_no_hint_but_submittable() -> None:
    commit = _annotated()
    assert commit.attention_reasons == []
    assert _texts(commit) == ["submittable"]
    assert attention_text(commit) == "submittable"


def test_project_strategy_decides_what_is_printed(stack_repo: Path) -> None:
    """A strategy that waives CI also removes the hint, not only the exit code."""
    registry = stack_repo / ".ger" / "attention"
    registry.mkdir(parents=True)
    (registry / "registry.py").write_text(
        "def _waive_ci(*, commit, chain_blocked):\n    return ['unresolved-comments']\nSTRATEGIES = {'p': _waive_ci}\n",
        encoding="utf-8",
    )
    clear_extension_registry_cache()
    clear_attention_strategy_cache()

    commit = replace(_STABLE, verified=-1, comments_unresolved=2)
    annotate_attention([commit], cwd=stack_repo, project="p", settings=Settings.from_map({}))

    assert commit.attention_reasons == [Attention.UNRESOLVED_COMMENTS]
    assert _texts(commit) == ["2 unresolved comments"]
    assert attention_text(commit) == "2 unresolved comments"
