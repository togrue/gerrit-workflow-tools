"""Unit tests for edit-attention helpers (``ger log`` attention reuse)."""

from __future__ import annotations

from gerrit_workflow_tools.core.gerrit_change_status import (
    LogCommit,
    PatchsetStatus,
    annotate_attention,
    commit_needs_edit_attention,
    determine_attention,
    first_commit_needing_edit_attention,
)


def _commit(**kwargs) -> LogCommit:
    defaults = {
        "sha": "a" * 40,
        "short_sha": "aaaaaaa",
        "summary": "subj",
        "change_id": "Iabc",
        "pushed": True,
        "abandoned": False,
        "patchset_status": PatchsetStatus.ACTIVE,
        "verified": 1,
        "code_review": 2,
        "comments_unresolved": 0,
    }
    defaults.update(kwargs)
    return LogCommit(**defaults)


def test_commit_needs_edit_attention_filters_log_reasons() -> None:
    ci = _commit(verified=-1)
    annotate_attention([ci])
    assert "ci-failed" in ci.attention_reasons
    assert commit_needs_edit_attention(ci)

    review_only = _commit(code_review=1)
    annotate_attention([review_only])
    assert "awaiting-review" in review_only.attention_reasons
    assert not commit_needs_edit_attention(review_only)

    comments = _commit(comments_unresolved=2)
    annotate_attention([comments])
    assert commit_needs_edit_attention(comments)


def test_first_commit_needing_edit_attention_oldest_first() -> None:
    oldest = _commit(sha="1" * 40, short_sha="1111111", verified=-1)
    newest = _commit(sha="2" * 40, short_sha="2222222", comments_unresolved=1)
    annotate_attention([oldest, newest])
    picked = first_commit_needing_edit_attention([oldest, newest])
    assert picked is oldest


def test_determine_attention_ci_failed_reason() -> None:
    c = _commit(verified=-1)
    assert "ci-failed" in determine_attention(c, chain_blocked=False)
