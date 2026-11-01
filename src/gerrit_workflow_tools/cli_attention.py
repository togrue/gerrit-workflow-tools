"""Shared CLI helpers for edit-attention targeting (``ger edit`` / ``show`` / ``sha``)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.core.annotated_stack import branches_needing_upstream, resolve_rev_range
from gerrit_workflow_tools.core.attention_cursor import EditAttentionMode
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.edit_attention_walk import resolve_attention_query
from gerrit_workflow_tools.core.gerrit.rest import GerritRest
from gerrit_workflow_tools.core.gerrit_change_status import LogCommit
from gerrit_workflow_tools.core.upstream_interactive import require_branch_upstream


def add_attention_query_args(parser: argparse.ArgumentParser) -> None:
    """Add ``--first-attention`` and ``-n`` / ``--next-attention`` (mutually exclusive)."""
    attn = parser.add_mutually_exclusive_group()
    attn.add_argument(
        "--first-attention",
        action="store_true",
        help="Target the oldest stack commit with edit attention (unresolved comments or CI failed).",
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
