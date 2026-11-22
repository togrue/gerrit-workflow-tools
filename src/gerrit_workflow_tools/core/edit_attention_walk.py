"""Edit-attention walk: load the annotated stack and pick resume/next/first targets.

Builds on :mod:`gerrit_workflow_tools.core.attention_cursor` (cursor storage) and
:mod:`gerrit_workflow_tools.core.attention` (edit-attention reasons).
"""

from __future__ import annotations

from dataclasses import dataclass

from gerrit_workflow_tools.core.attention import (
    NoEditAttentionError,
    commit_needs_edit_attention,
    find_commit_by_change_id,
    first_commit_needing_edit_attention,
)
from gerrit_workflow_tools.core.attention_cursor import (
    EditAttentionMode,
    load_branch_cursor,
    prepare_usable_cursor,
    resolve_edit_attention_target,
    write_branch_cursor,
)


# Re-export for CLI callers.
__all__ = [
    "CursorStatus",
    "change_id_for_stack_sha",
    "describe_cursor_status",
    "load_edit_attention_stack",
    "resolve_attention_query",
    "resolve_edit_attention_commit",
    "resolve_first_edit_attention_sha",
]
from gerrit_workflow_tools.core.gerrit_change_status import LogCommit
from gerrit_workflow_tools.core.run_context import RunContext


_NO_EDIT_ATTENTION = "no commit needs edit attention (unresolved comments or build failed)"


def load_edit_attention_stack(run: RunContext):
    """Load the annotated first-parent stack for edit-attention targeting.

    Does not prompt for upstream; callers that need a configured upstream must
    require it before calling.
    """
    from gerrit_workflow_tools.core.annotated_stack import load_annotated_stack
    from gerrit_workflow_tools.core.git_run import GitError

    stack_view = load_annotated_stack(run, None)
    if not stack_view.commits:
        raise GitError("no commits in stack")
    # A walk over unknown Gerrit state would report "nothing needs attention": not true, just unknown.
    stack_view.require_gerrit()
    return stack_view


def resolve_first_edit_attention_sha(run: RunContext) -> str:
    """Return full SHA of the oldest commit with unresolved comments or a failed build."""
    stack_view = load_edit_attention_stack(run)
    target = first_commit_needing_edit_attention(stack_view.commits)
    if target is None:
        raise NoEditAttentionError(_NO_EDIT_ATTENTION)
    return target.sha


def resolve_edit_attention_commit(
    run: RunContext,
    *,
    mode: EditAttentionMode,
    branch: str | None,
    after_change_id: str | None = None,
    update_cursor: bool = True,
) -> tuple[LogCommit, list[str]]:
    """Resolve an edit-attention target and optionally advance the attention cursor."""
    stack_view = load_edit_attention_stack(run)
    notes: list[str] = []
    cursor_change_id: str | None = None
    branch_name = branch or "HEAD"

    if mode != "first":
        prepared = prepare_usable_cursor(
            run.cwd,
            branch=branch_name,
            commits=stack_view.commits,
            settings=run.settings,
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
        raise NoEditAttentionError(_NO_EDIT_ATTENTION)

    if update_cursor and target.change_id and branch is not None:
        write_branch_cursor(run.cwd, branch, target.change_id, action=mode, sha=target.sha)

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


def describe_cursor_status(run: RunContext, *, branch: str | None) -> CursorStatus:
    """Build a :class:`CursorStatus` for the working branch (may load the stack)."""
    from gerrit_workflow_tools.core.attention_cursor import format_iso_utc

    settings = run.settings
    branch_name = branch or "HEAD"
    cursor = load_branch_cursor(settings, branch_name)
    if cursor is None:
        return CursorStatus(
            branch=branch_name,
            change_id=None,
            last_attention_change_id=settings.last_attention_change_id,
        )
    stack_view = load_edit_attention_stack(run)
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


def change_id_for_stack_sha(run: RunContext, sha: str) -> str | None:
    """Return the Change-Id of *sha* in the annotated stack, if present."""
    stack_view = load_edit_attention_stack(run)
    matched = next((c for c in stack_view.commits if c.sha == sha), None)
    return matched.change_id if matched else None


def resolve_attention_query(
    run: RunContext,
    *,
    branch: str | None,
    mode: EditAttentionMode,
    after_changeish: str | None = None,
) -> tuple[LogCommit, list[str]]:
    """Resolve an edit-attention target for read-only queries (``ger show`` / ``ger sha``).

    Does not update the attention cursor. *after_changeish* is optional for ``next``.
    """
    from gerrit_workflow_tools.core.gerrit.change_resolution import ChangeResolutionError, resolve_stack_changeish

    after_change_id: str | None = None
    if after_changeish:
        after = resolve_stack_changeish(
            run.cwd,
            after_changeish,
            service=run.gerrit,
            branch=branch,
            require_in_stack=True,
        )
        after_change_id = change_id_for_stack_sha(run, after.sha)
        if not after_change_id:
            raise ChangeResolutionError("REV has no Change-Id; cannot use --next-attention AFTER")
    return resolve_edit_attention_commit(
        run,
        mode=mode,
        branch=branch,
        after_change_id=after_change_id,
        update_cursor=False,
    )
