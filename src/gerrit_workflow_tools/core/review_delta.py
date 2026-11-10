"""The **review delta**: what changed in a change since the patchset you last reviewed.

"Reviewed" is the newest patchset you posted anything on — a vote, a comment, a reply. Gerrit
records that as a change message of yours carrying the patchset number.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gerrit_workflow_tools.core.commit_diff import FileChange, commit_diff, diff_between
from gerrit_workflow_tools.core.gerrit.rest import self_account_id
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.git_run import commit_sha_or_none
from gerrit_workflow_tools.core.review_checkout import fetch_patchset
from gerrit_workflow_tools.core.review_target import patchset_to_review


@dataclass(frozen=True)
class ReviewDelta:
    """The current patchset of a change against the one you last reviewed."""

    current_patch_set: int
    commit: str
    """The current patchset's commit."""
    reviewed_patch_set: int | None = None
    """``None``: you have not reviewed this change (or asked for a patchset it does not have)."""
    base: str | None = None
    """The reviewed patchset's commit; ``None`` when there is nothing to compare."""
    rebased: bool = False
    """The two patchsets sit on different parents: :attr:`files` is narrowed to what either touched."""
    files: list[FileChange] = field(default_factory=list)

    @property
    def up_to_date(self) -> bool:
        """You reviewed the current patchset: nothing is new."""
        return self.reviewed_patch_set == self.current_patch_set


def last_reviewed_patch_set(messages: Sequence[Mapping[str, Any]], account_id: int | None) -> int | None:
    """The newest patchset *account_id* posted a change message on, or ``None``."""
    if account_id is None:
        return None
    numbers = [
        number
        for message in messages
        if isinstance(author := message.get("author"), Mapping)
        and author.get("_account_id") == account_id
        and isinstance(number := message.get("_revision_number"), int)
    ]
    return max(numbers, default=None)


def patchset_delta_files(cwd: Path | str, old: str, new: str) -> tuple[bool, list[FileChange]]:
    """``(rebased, files)`` that differ between two patchset commits of one change.

    On the same parent that is the plain diff. After a rebase the plain diff would also show
    everything that landed underneath, so it is narrowed to the files either patchset touched;
    within those files, upstream edits still show.
    """
    if commit_sha_or_none(cwd, f"{old}^") == commit_sha_or_none(cwd, f"{new}^"):
        return False, diff_between(cwd, old, new)
    touched: dict[str, None] = {}
    for change in (*commit_diff(cwd, old).files, *commit_diff(cwd, new).files):
        touched[change.path] = None
        if change.old_path:
            touched[change.old_path] = None
    return True, diff_between(cwd, old, new, list(touched)) if touched else []


def load_review_delta(
    service: GerritService, cwd: Path | str, changeish: str, *, since: int | None = None
) -> ReviewDelta:
    """The review delta of *changeish*; *since* names the patchset to compare with instead of asking Gerrit.

    Fetches both patchsets from ``gerrit.remote`` when they are not in the repository.
    """
    current, _url = patchset_to_review(service, cwd, changeish, None)
    reviewed = since
    if reviewed is None:
        reviewed = last_reviewed_patch_set(service.rest.get_messages(current.key), self_account_id(service.rest))
    if reviewed is None or reviewed >= current.patch_set:
        return ReviewDelta(
            current_patch_set=current.patch_set,
            commit=current.commit,
            reviewed_patch_set=None if reviewed is None else current.patch_set,
        )
    old, _url = patchset_to_review(service, cwd, changeish, reviewed)
    remote = service.settings.gerrit_remote
    for patchset in (old, current):
        if commit_sha_or_none(cwd, patchset.commit) is None:
            fetch_patchset(cwd, remote, patchset.number, patchset.patch_set, patchset.commit)
    rebased, files = patchset_delta_files(cwd, old.commit, current.commit)
    return ReviewDelta(
        current_patch_set=current.patch_set,
        commit=current.commit,
        reviewed_patch_set=reviewed,
        base=old.commit,
        rebased=rebased,
        files=files,
    )
