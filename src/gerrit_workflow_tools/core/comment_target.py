"""Which change, commit and patchset a new comment is written on.

Spec: docu/spec/commands/comment.md § Where it goes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gerrit_workflow_tools.core.gerrit.change_view import ChangeView
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.gerrit.stack_context import ChangeResolutionError
from gerrit_workflow_tools.core.gerrit_show import resolve_show_commit_row
from gerrit_workflow_tools.core.git_run import commit_sha_or_none


class MissingChangeIdError(ChangeResolutionError):
    """The commit a comment names has no valid Change-Id, so it has no triplet to key it by."""


@dataclass(frozen=True)
class CommentTarget:
    """The change a new comment belongs to, and the version of it the location refers to."""

    key: str
    """The change's triplet."""
    change_id: str
    commit: str | None
    """The local commit the location is checked against; ``None`` when Gerrit's is not fetched."""
    patch_set: int | None
    """The Gerrit patchset that *commit* is (or the current one, without a local commit).
    ``None`` when Gerrit has no patchset with these lines: not pushed, or rewritten locally."""
    change_url: str | None
    summary: str

    @property
    def on_gerrit(self) -> bool:
        """Whether a Gerrit draft can hold a comment on these lines."""
        return self.patch_set is not None


def resolve_comment_target(
    service: GerritService,
    cwd: Path | str,
    changeish: str | None,
    *,
    branch: str | None,
) -> CommentTarget:
    """The target for *changeish* (``HEAD`` when ``None``), in the full changeish grammar."""
    resolved = resolve_show_commit_row(cwd, changeish or "HEAD", service.rest, settings=service.settings, branch=branch)
    row = resolved.row
    change_id = row.valid_change_id
    if not change_id:
        raise MissingChangeIdError(f"commit {row.short_sha} has no Change-Id")
    selected = resolved.resolution.selected
    key = selected.triplet if selected is not None else service.cache_key(change_id)
    local = row.sha if resolved.is_local_commit else (commit_sha_or_none(cwd, row.sha) if row.sha else None)
    return target_for_commit(
        service, key, change_id, local, summary=row.subject, gerrit_current=not resolved.is_local_commit
    )


def target_for_commit(
    service: GerritService,
    key: str,
    change_id: str,
    commit: str | None,
    *,
    summary: str,
    gerrit_current: bool = False,
) -> CommentTarget:
    """The target for local *commit* of change *key*: which patchset it is on Gerrit, if any.

    *gerrit_current* says the commit was taken from Gerrit's current revision, so the
    change's current patchset applies even when the commit is not fetched locally.
    """
    payload = service.changes.get_payloads([key]).get(key)
    view = ChangeView.from_payload(payload) if payload else None
    patch_set: int | None = None
    if view is not None:
        revision = view.revisions.get(commit.lower()) if commit else None
        if revision is not None:
            patch_set = revision.number
        elif gerrit_current:
            patch_set = view.current_revision_number
    return CommentTarget(
        key=key,
        change_id=change_id,
        commit=commit,
        patch_set=patch_set,
        change_url=view.url(service.web_base) if view is not None else None,
        summary=summary,
    )
