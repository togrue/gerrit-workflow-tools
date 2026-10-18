"""Edit-attention walk: load the annotated stack and pick resume/next/first targets.

Builds on :mod:`gerrit_workflow_tools.core.attention_cursor` (cursor storage) and
:mod:`gerrit_workflow_tools.core.attention` (edit-attention reasons).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gerrit_workflow_tools.core.attention import (
    commit_needs_edit_attention,
    find_commit_by_change_id,
    first_commit_needing_edit_attention,
)
from gerrit_workflow_tools.core.attention_cursor import (
    CursorAction,
    EditAttentionMode,
    load_branch_cursor,
    prepare_usable_cursor,
    resolve_edit_attention_target,
    write_branch_cursor,
)
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit_change_status import LogCommit


_NO_EDIT_ATTENTION = "no commit needs edit attention (unresolved comments or build failed)"


def load_edit_attention_stack(
    cwd: Path | str | None,
    *,
    settings: Settings,
    gerrit: object | None = None,
):
    """Load the annotated first-parent stack for edit-attention targeting.

    Does not prompt for upstream; callers that need a configured upstream must
    require it before calling. *gerrit* is a ``GerritRest`` when provided.
    """
    from gerrit_workflow_tools.core.annotated_stack import load_annotated_stack, resolve_rev_range
    from gerrit_workflow_tools.core.gerrit.change_resolution import ChangeResolutionError
    from gerrit_workflow_tools.core.gerrit.rest import GerritApiError
    from gerrit_workflow_tools.core.git_run import GitError

    rev_range = resolve_rev_range(cwd, None, settings=settings)
    try:
        stack_view = load_annotated_stack(cwd, rev_range, settings=settings, gerrit=gerrit)  # type: ignore[arg-type]
    except (ValueError, ChangeResolutionError, GerritApiError) as e:
        raise GitError(f"could not load stack commits: {e}") from e
    if not stack_view.commits:
        raise GitError("no commits in stack")
    return stack_view


def resolve_first_edit_attention_sha(
    cwd: Path | str | None, *, settings: Settings, gerrit: object | None = None
) -> str:
    """Return full SHA of the oldest commit with unresolved comments or a failed build."""
    from gerrit_workflow_tools.core.git_run import GitError

    stack_view = load_edit_attention_stack(cwd, settings=settings, gerrit=gerrit)
    target = first_commit_needing_edit_attention(stack_view.commits)
    if target is None:
        raise GitError(_NO_EDIT_ATTENTION)
    return target.sha


def resolve_edit_attention_commit(
    cwd: Path | str | None,
    *,
    settings: Settings,
    mode: EditAttentionMode,
    branch: str | None,
    after_change_id: str | None = None,
    gerrit: object | None = None,
    update_cursor: bool = True,
) -> tuple[LogCommit, list[str]]:
    """Resolve an edit-attention target and optionally advance the attention cursor."""
    from gerrit_workflow_tools.core.git_run import GitError

    stack_view = load_edit_attention_stack(cwd, settings=settings, gerrit=gerrit)
    notes: list[str] = []
    cursor_change_id: str | None = None
    branch_name = branch or "HEAD"

    if mode != "first":
        prepared = prepare_usable_cursor(
            cwd,
            branch=branch_name,
            commits=stack_view.commits,
            settings=settings,
        )
        notes.extend(prepared.notes)
        cursor_change_id = prepared.change_id

    target = resolve_edit_attention_target(
        stack_view.commits,
        mode=mode,
        cursor_change_id=cursor_change_id,
        after_change_id=after_change_id,
    )
    if target is None:
        raise GitError(_NO_EDIT_ATTENTION)

    if update_cursor and target.change_id and branch is not None:
        action: CursorAction = "first" if mode == "first" else ("next" if mode == "next" else "resume")
        write_branch_cursor(cwd, branch, target.change_id, action=action, sha=target.sha)

    return target, notes


@dataclass(frozen=True)
class CursorStatus:
    """Snapshot of the attention cursor for display."""

    branch: str
    change_id: str | None
    touched_at: str | None = None
    in_stack: bool | None = None
    edit_attention: bool | None = None
    short_sha: str | None = None
    last_attention_change_id: str | None = None


def describe_cursor_status(
    cwd: Path | str | None,
    *,
    settings: Settings,
    branch: str | None,
    gerrit: object | None = None,
) -> CursorStatus:
    """Build a :class:`CursorStatus` for the working branch (may load the stack)."""
    from gerrit_workflow_tools.core.attention_cursor import format_iso_utc

    branch_name = branch or "HEAD"
    cursor = load_branch_cursor(settings, branch_name)
    if cursor is None:
        return CursorStatus(
            branch=branch_name,
            change_id=None,
            last_attention_change_id=settings.last_attention_change_id,
        )
    stack_view = load_edit_attention_stack(cwd, settings=settings, gerrit=gerrit)
    found = find_commit_by_change_id(stack_view.commits, cursor.change_id)
    return CursorStatus(
        branch=branch_name,
        change_id=cursor.change_id,
        touched_at=format_iso_utc(cursor.touched_at),
        in_stack=found is not None,
        edit_attention=commit_needs_edit_attention(found) if found else False,
        short_sha=found.short_sha if found else None,
        last_attention_change_id=settings.last_attention_change_id,
    )


def change_id_for_stack_sha(
    cwd: Path | str | None,
    sha: str,
    *,
    settings: Settings,
    gerrit: object | None = None,
) -> str | None:
    """Return the Change-Id of *sha* in the annotated stack, if present."""
    stack_view = load_edit_attention_stack(cwd, settings=settings, gerrit=gerrit)
    matched = next((c for c in stack_view.commits if c.sha == sha), None)
    return matched.change_id if matched else None
