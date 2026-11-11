"""Shared CLI helpers for edit-attention targeting (``ger edit`` / ``show`` / ``sha``) and the attention cursor."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from gerrit_workflow_tools.cli_common import ExitCode, print_resolution_note
from gerrit_workflow_tools.core.annotated_stack import branches_needing_upstream, resolve_rev_range
from gerrit_workflow_tools.core.attention_cursor import (
    EditAttentionMode,
    load_branch_cursor,
    read_cursor_history,
    write_branch_cursor,
)
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.edit_attention_walk import (
    change_id_for_stack_sha,
    describe_cursor_status,
    resolve_attention_query,
)
from gerrit_workflow_tools.core.gerrit.change_resolution import StackCommit, resolve_stack_changeish
from gerrit_workflow_tools.core.gerrit.rest import GerritRest
from gerrit_workflow_tools.core.gerrit_change_status import LogCommit
from gerrit_workflow_tools.core.git_run import GitError
from gerrit_workflow_tools.core.upstream_interactive import require_branch_upstream


def add_attention_query_args(parser: argparse.ArgumentParser) -> None:
    """Add ``--first-attention`` and ``-n`` / ``--next-attention`` (mutually exclusive)."""
    attn = parser.add_mutually_exclusive_group()
    attn.add_argument(
        "--first-attention",
        action="store_true",
        help="Target the oldest stack commit with edit attention (unresolved comments or CI failed; a vote alone "
        "does not count).",
    )
    attn.add_argument(
        "-n",
        "--next-attention",
        action="store_true",
        help=(
            "Target the next edit-attention commit tip-ward of the attention cursor "
            "(or of a single REV when given). Does not move the cursor."
        ),
    )


def require_stack_upstream(cwd: Path, settings: Settings, rev_range: str | None = None) -> bool:
    """Prompt for upstream on *rev_range*'s branches (default: the stack); False if declined."""
    rev_range = rev_range or resolve_rev_range(cwd, None, settings=settings)
    return all(
        require_branch_upstream(cwd, branch, settings=settings)
        for branch in branches_needing_upstream(cwd, rev_range, settings=settings)
    )


def resolve_attention_query_or_exit(
    cwd: Path,
    args: argparse.Namespace,
    *,
    settings: Settings,
    branch: str | None,
    after_changeish: str | None,
    gerrit: GerritRest | None = None,
) -> LogCommit | int:
    """Resolve an attention query, or return an :class:`ExitCode` int on upstream decline."""
    if not require_stack_upstream(cwd, settings):
        return int(ExitCode.ATTENTION)
    mode: EditAttentionMode = "first" if args.first_attention else "next"
    target, notes = resolve_attention_query(
        cwd,
        settings=settings,
        branch=branch,
        mode=mode,
        after_changeish=after_changeish,
        gerrit=gerrit,
    )
    for note in notes:
        print(note, file=sys.stderr)
    return target


def show_revs_with_attention(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    *,
    cwd: Path,
    settings: Settings,
    branch: str | None,
    gerrit: GerritRest | None = None,
) -> list[str] | int:
    """Return REV list for ``ger show``, applying attention flags when set.

    Returns an :class:`ExitCode` int when upstream is declined.
    """
    revs = list(args.revs)
    if not (args.first_attention or args.next_attention):
        return revs
    if args.stack:
        parser.error("cannot combine --stack with --first-attention / --next-attention")
    if args.first_attention and revs:
        parser.error("cannot use REV with --first-attention")
    if args.next_attention and len(revs) > 1:
        parser.error("--next-attention accepts at most one REV (AFTER)")
    after = revs[0] if args.next_attention and revs else None
    resolved = resolve_attention_query_or_exit(
        cwd,
        args,
        settings=settings,
        branch=branch,
        after_changeish=after,
        gerrit=gerrit,
    )
    if isinstance(resolved, int):
        return resolved
    return [resolved.sha]


def resolve_in_stack(
    cwd: Path,
    rev: str,
    *,
    settings: Settings,
    branch: str | None,
    gerrit: GerritRest | None,
) -> StackCommit:
    target = resolve_stack_changeish(cwd, rev, settings=settings, gerrit=gerrit, branch=branch, require_in_stack=True)
    print_resolution_note(target.resolution)
    return target


def print_cursor_status(cwd: Path, *, settings: Settings, branch: str | None, gerrit: GerritRest | None) -> int:
    status = describe_cursor_status(cwd, settings=settings, branch=branch, gerrit=gerrit)
    if status.change_id is None:
        print(f"attention cursor: (none) on branch {status.branch}")
        if status.last_attention_change_id:
            print(f"repo lastAttentionChangeId: {status.last_attention_change_id}")
        return 0
    print(f"attention cursor: {status.change_id}")
    print(f"  branch: {status.branch}")
    print(f"  touchedAt: {status.touched_at}")
    print(f"  inStack: {status.in_stack}")
    print(f"  editAttention: {status.edit_attention}")
    if status.short_sha:
        print(f"  shortSha: {status.short_sha}")
    return 0


def print_cursor_history(cwd: Path) -> int:
    events = read_cursor_history(cwd, limit=50)
    if not events:
        print("attention cursor history: (empty)")
        return 0
    for event in events:
        sha = f" {event.sha}" if event.sha else ""
        print(f"{event.ts}  {event.branch}  {event.action}  {event.change_id}{sha}")
    return 0


def set_cursor_from_changeish(
    cwd: Path,
    changeish: str,
    *,
    settings: Settings,
    branch: str | None,
    gerrit: GerritRest | None,
) -> int:
    if branch is None:
        raise GitError("detached HEAD: cannot set attention cursor without a branch")
    target = resolve_in_stack(cwd, changeish, settings=settings, branch=branch, gerrit=gerrit)
    change_id = change_id_for_stack_sha(cwd, target.sha, settings=settings, gerrit=gerrit)
    if not change_id:
        raise GitError("target commit has no Change-Id; cannot set attention cursor")
    current = load_branch_cursor(settings, branch)
    if current is not None and current.change_id == change_id:
        print(f"attention cursor already at {change_id}")
        return 0
    write_branch_cursor(cwd, branch, change_id, action="set", sha=target.sha)
    print(f"attention cursor set to {change_id}")
    return 0
