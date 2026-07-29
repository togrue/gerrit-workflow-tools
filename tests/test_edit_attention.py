"""Unit tests for edit-attention helpers (``ger log`` attention reuse)."""

from __future__ import annotations

from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_edit import resolve_first_edit_attention_sha
from gerrit_workflow_tools.core.annotated_stack import load_annotated_stack, resolve_rev_range
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit_change_status import (
    LogCommit,
    PatchsetStatus,
    annotate_attention,
    commit_needs_edit_attention,
    first_commit_needing_edit_attention,
)
from gerrit_workflow_tools.core.git_run import GitError, git
from tests.change_store import ChangeStore
from tests.cli_gerrit_mocks import change_info_for_sha
from tests.fixtures import make_repo_with_merged_side_branch


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
def test_first_attention_commit_ignores_merged_side_branches(tmp_path: Path) -> None:
    """``--first-attention-commit`` must see the same commits ``ger log`` does.

    Its help promises "same detection as ger log", and ger log walks the first-parent
    chain (Gerrit relation-chain semantics). A commit that is only reachable through a
    merge parent is not in the stack ger log shows, so edit must not select it either.
    """
    repo = make_repo_with_merged_side_branch(tmp_path / "r")
    git("config", "gerrit.webUrl", "https://g.example", cwd=repo)
    git("config", "gerrit.project", "testproj", cwd=repo)

    # Only the side-branch commits need attention; nothing on the first-parent chain does.
    side_cids = ["I" + "2" * 40, "I" + "3" * 40]
    payloads: dict[str, dict] = {}
    for number, cid in enumerate(side_cids, start=200):
        payload = change_info_for_sha("0" * 40, cid, number=number, unresolved_comment_count=3)
        payloads[str(payload["id"])] = payload
    for number, cid in enumerate(["I" + "1" * 40, "I" + "4" * 40], start=300):
        payload = change_info_for_sha("0" * 40, cid, number=number)
        payloads[str(payload["id"])] = payload

    store = ChangeStore(payloads, web_base="https://g.example")

    with pytest.raises(GitError, match="no commit needs edit attention"):
        resolve_first_edit_attention_sha(repo, settings=Settings.from_cwd(repo), gerrit=store)


def test_follow_merges_would_have_found_the_side_branch_commit(tmp_path: Path) -> None:
    """Counterpart to the above: the side commit *is* visible with full-DAG traversal.

    Pins that the previous test passes because of first-parent traversal, not because the
    payloads failed to signal attention.
    """
    repo = make_repo_with_merged_side_branch(tmp_path / "r")
    git("config", "gerrit.webUrl", "https://g.example", cwd=repo)
    git("config", "gerrit.project", "testproj", cwd=repo)

    cid = "I" + "2" * 40
    payload = change_info_for_sha("0" * 40, cid, number=200, unresolved_comment_count=3)
    store = ChangeStore({str(payload["id"]): payload}, web_base="https://g.example")

    stack = load_annotated_stack(
        repo,
        resolve_rev_range(repo, None, settings=Settings.from_cwd(repo)),
        settings=Settings.from_cwd(repo),
        first_parent=False,
        gerrit=store,
    )

    flagged = [c for c in stack.commits if c.comments_unresolved > 0]
    assert [c.change_id for c in flagged] == [cid]
