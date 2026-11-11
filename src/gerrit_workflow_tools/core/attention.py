"""Attention: the verdict that a commit needs the author's action.

The one owner of that verdict. ``ger log``'s exit code, ``ger edit --first-attention-commit``,
the trailing ``# …`` hints, the rebase todo and the inbox all read the reasons computed here,
so a project's ``.ger/attention`` strategy changes what is printed as well as what is decided.
Render maps reasons to text; it never re-derives them from votes or patchset state.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit_change_status import LogCommit, PatchsetStatus
from gerrit_workflow_tools.core.git_run import GitError


class NoEditAttentionError(GitError):
    """An attention walk found nothing to do: the normal end of a walk, not a git failure."""


class Attention:  # pylint: disable=too-few-public-methods
    """Reason names. Plain strings, because attention strategies return plain strings."""

    MISSING_CHANGE_ID = "missing-change-id"
    ABANDONED = "abandoned"
    NOT_PUSHED = "not-pushed"
    MERGED_DRIFT = "merged-drift"
    MERGED_UNKNOWN = "merged-unknown"
    CHAIN_BLOCKED = "chain-blocked"
    AHEAD_OF_GERRIT = "ahead-of-gerrit"
    OUTDATED_PATCHSET = "outdated-patchset"
    CI_FAILED = "ci-failed"
    NO_REVIEWERS = "no-reviewers"
    REVIEW_ISSUES = "review-issues"
    AWAITING_REVIEW = "awaiting-review"
    UNRESOLVED_COMMENTS = "unresolved-comments"
    WIP = "wip"
    GERRIT_UNKNOWN = "gerrit-unknown"
    """Gerrit could not be asked, so this commit's review state is unknown."""


# Attention that warrants amending the commit (``ger edit --first-attention-commit``).
EDIT_ATTENTION_REASONS = frozenset({Attention.UNRESOLVED_COMMENTS, Attention.CI_FAILED})


def ci_failed(verified: int | None, ci_failures: Sequence[str] = ()) -> bool:
    """A failed build: any failed Checks run, or a negative ``Verified`` vote."""
    return bool(ci_failures) or (verified is not None and verified <= -1)


_MERGED_REASON = {
    PatchsetStatus.MERGED_SAME: None,
    PatchsetStatus.MERGED_DRIFT: Attention.MERGED_DRIFT,
    PatchsetStatus.MERGED_UNKNOWN: Attention.MERGED_UNKNOWN,
}


def _settled_reasons(commit: LogCommit, *, chain_blocked: bool) -> list[str] | None:
    """Reasons for a commit with no open review to judge, or ``None`` when it has one."""
    if not commit.change_id and commit.patchset_status != PatchsetStatus.MERGED_SAME:
        # Prefer missing-change-id over only not-pushed: overlay cannot match Gerrit without a footer.
        return [Attention.MISSING_CHANGE_ID]
    if commit.patchset_status == PatchsetStatus.UNKNOWN:
        return [Attention.GERRIT_UNKNOWN]
    if commit.abandoned:
        return [Attention.ABANDONED]
    if commit.patchset_status == PatchsetStatus.ABSENT:
        return [Attention.NOT_PUSHED]
    if commit.patchset_status in _MERGED_REASON:
        merged = _MERGED_REASON[commit.patchset_status]
        reasons = [merged] if merged else []
        if chain_blocked:
            reasons.append(Attention.CHAIN_BLOCKED)
        return reasons
    return None


def determine_attention(commit: LogCommit, *, chain_blocked: bool) -> list[str]:
    """Return reasons why this commit needs attention (empty = stable)."""
    settled = _settled_reasons(commit, chain_blocked=chain_blocked)
    if settled is not None:
        return settled
    reasons: list[str] = [Attention.WIP] if commit.wip else []
    if commit.patchset_status == PatchsetStatus.NEWER:
        reasons.append(Attention.AHEAD_OF_GERRIT)
    if commit.patchset_status == PatchsetStatus.OUTDATED:
        reasons.append(Attention.OUTDATED_PATCHSET)
    if ci_failed(commit.verified, commit.ci_failures):
        reasons.append(Attention.CI_FAILED)
    # A WIP change is not asking for review yet, so missing reviewers and votes say nothing.
    if len(commit.reviewers) == 0 and not commit.wip:
        reasons.append(Attention.NO_REVIEWERS)
    if commit.code_review is not None and commit.code_review < 0:
        reasons.append(Attention.REVIEW_ISSUES)
    if commit.code_review != 2 and not commit.wip:
        reasons.append(Attention.AWAITING_REVIEW)
    if commit.comments_unresolved > 0:
        reasons.append(Attention.UNRESOLVED_COMMENTS)
    if chain_blocked:
        reasons.append(Attention.CHAIN_BLOCKED)
    return reasons


def commit_blocks_chain_for_submittability(commit: LogCommit) -> bool:
    """Return whether this commit should block later commits in a dependency chain.

    Default rule: a pushed change that is not submittable on Gerrit blocks followers.

    **MERGED** changes use local equivalence semantics (v1):

    * ``merged-same`` — does **not** block (already integrated upstream).
    * ``merged-drift`` — **blocks** (local patch provably differs from merged revision).
    * ``merged-unknown`` — **blocks** (cannot prove equivalence; treat conservatively).

    Open (non-MERGED) changes keep the usual ``not submittable`` rule.
    """
    if commit.patchset_status == PatchsetStatus.MERGED_SAME:
        return False
    if commit.patchset_status in (PatchsetStatus.MERGED_DRIFT, PatchsetStatus.MERGED_UNKNOWN):
        return True
    if not commit.pushed:
        return False
    return not commit.submittable


def annotate_attention(
    commits: list[LogCommit],
    *,
    cwd: Path | str | None = None,
    project: str = "",
    settings: Settings | None = None,
    web_base: str | None = None,
) -> None:
    """Populate ``attention_reasons`` on each commit, including chain-blocking.

    Chain-blocking: an earlier pushed commit blocks later ones when the chain-block
    strategy (or built-in :func:`commit_blocks_chain_for_submittability`) says so.
    Same rules as ``ger log``.
    """
    from gerrit_workflow_tools.core.attention_strategy import (
        attention_reasons_via_registry,
        commit_blocks_chain_via_registry,
    )

    prefix_chain_blocks = False
    for commit in commits:
        chain_blocked = commit.pushed and prefix_chain_blocks
        commit.attention_reasons = attention_reasons_via_registry(
            cwd,
            project=project,
            commit=commit,
            chain_blocked=chain_blocked,
            settings=settings,
            web_base=web_base,
        )
        if commit.pushed and commit_blocks_chain_via_registry(
            cwd,
            project=project,
            commit=commit,
            settings=settings,
            web_base=web_base,
        ):
            prefix_chain_blocks = True


def commit_needs_edit_attention(commit: LogCommit) -> bool:
    """True when the commit has unresolved comments or a failed build (``ger log`` attention)."""
    return bool(EDIT_ATTENTION_REASONS.intersection(commit.attention_reasons))


def first_commit_needing_edit_attention(commits: list[LogCommit]) -> LogCommit | None:
    """Oldest commit in *commits* that needs edit attention, or ``None``."""
    for commit in commits:
        if commit_needs_edit_attention(commit):
            return commit
    return None


def find_commit_by_change_id(commits: Sequence[LogCommit], change_id: str) -> LogCommit | None:
    """Return the stack commit whose Change-Id matches *change_id* (case-insensitive).

    When several commits share the Change-Id, return the oldest. Callers that need
    ambiguity errors must check for duplicates themselves.
    """
    needle = change_id.casefold()
    for commit in commits:
        if commit.change_id and commit.change_id.casefold() == needle:
            return commit
    return None


def next_commit_needing_edit_attention_after(commits: list[LogCommit], after_change_id: str) -> LogCommit | None:
    """First edit-attention commit strictly tip-ward of *after_change_id*, or ``None``.

    If *after_change_id* is not in *commits*, returns ``None`` (caller treats the cursor
    as missing).
    """
    needle = after_change_id.casefold()
    seen = False
    for commit in commits:
        if not seen:
            if commit.change_id and commit.change_id.casefold() == needle:
                seen = True
            continue
        if commit_needs_edit_attention(commit):
            return commit
    return None
