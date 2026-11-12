"""The **review delta**: what changed in a change since the patchset you last reviewed.

"Reviewed" is the newest patchset you posted anything on — a vote, a comment, a reply. Gerrit
records that as a change message of yours carrying the patchset number.

The same comparison serves any two versions of one change: an older patchset against a newer
one, or a patchset against the local commit that carries the change.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from gerrit_workflow_tools.core.commit_diff import FileChange, commit_diff, diff_between
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.gerrit_patchset import fetch_patchset, resolve_change
from gerrit_workflow_tools.core.git_run import commit_sha_or_none


@dataclass(frozen=True)
class ReviewDelta:
    """The current patchset of a change against the one you last reviewed."""

    current_patch_set: int
    commit: str
    """The commit compared: the current patchset's, unless another patchset or a local commit was asked for."""
    reviewed_patch_set: int | None = None
    """``None``: you have not reviewed this change (or asked for a patchset it does not have)."""
    base: str | None = None
    """The reviewed patchset's commit; ``None`` when there is nothing to compare."""
    target_patch_set: int | None = None
    """The patchset :attr:`commit` is; ``None`` when it is a local commit."""
    rebased: bool = False
    """The two patchsets sit on different parents: :attr:`files` is narrowed to what either touched."""
    files: list[FileChange] = field(default_factory=list)

    @property
    def up_to_date(self) -> bool:
        """You reviewed the patchset compared: nothing is new."""
        return self.target_patch_set is not None and self.reviewed_patch_set == self.target_patch_set


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
    service: GerritService,
    cwd: Path | str,
    changeish: str,
    *,
    since: int | Literal["current"] | None = None,
    upto: int | None = None,
    local_commit: str | None = None,
) -> ReviewDelta:
    """The review delta of *changeish*: the patchset you last reviewed against the current one.

    *since* names the patchset to compare from instead of asking Gerrit which you reviewed
    (``"current"``: Gerrit's current one). The comparison runs to patchset *upto*, or to
    *local_commit* (the commit of the local stack that carries the change), else to the current
    patchset. Fetches the patchsets from ``gerrit.remote`` when they are not in the repository.
    """
    change = resolve_change(service, cwd, changeish)
    current = change.patchset()
    target = None if local_commit else change.patchset(upto)
    commit = local_commit or (target.commit if target else current.commit)
    reviewed = current.patch_set if since == "current" else since
    if reviewed is None:
        reviewed = last_reviewed_patch_set(service.rest.get_messages(current.key), service.self_account_id())
    known = ReviewDelta(
        current_patch_set=current.patch_set,
        commit=commit,
        target_patch_set=target.patch_set if target else None,
    )
    # A patchset is compared with an older one only; a local commit with any patchset Gerrit has.
    newest = target.patch_set - 1 if target else current.patch_set
    if reviewed is None:
        return known
    if reviewed > newest:
        return replace(known, reviewed_patch_set=target.patch_set if target else None)
    old = change.patchset(reviewed)
    remote = service.settings.gerrit_remote
    for patchset in (old, target):
        if patchset is not None and commit_sha_or_none(cwd, patchset.commit) is None:
            fetch_patchset(cwd, remote, patchset)
    rebased, files = patchset_delta_files(cwd, old.commit, commit)
    return replace(known, reviewed_patch_set=reviewed, base=old.commit, rebased=rebased, files=files)
