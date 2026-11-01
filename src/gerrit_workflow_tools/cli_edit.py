"""CLI for ``ger edit`` / ``ger reword``: interactive rebase that edits, rewords, or drops one stack commit."""

from __future__ import annotations

import argparse
import logging
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Literal

from gerrit_workflow_tools.cli_attention import require_stack_upstream
from gerrit_workflow_tools.cli_common import (
    ExitCode,
    add_verbose_and_debug_log_args,
    init_cli_runtime,
    print_resolution_note,
    run_cli_command,
)
from gerrit_workflow_tools.core.attention_cursor import (
    EditAttentionMode,
    read_cursor_history,
    write_branch_cursor,
)
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.edit_attention_walk import (
    change_id_for_stack_sha,
    describe_cursor_status,
    resolve_edit_attention_commit,
    resolve_first_edit_attention_sha,
)
from gerrit_workflow_tools.core.gerrit.change_resolution import StackCommit, resolve_stack_changeish
from gerrit_workflow_tools.core.gerrit.rest import GerritRest
from gerrit_workflow_tools.core.git_run import GitError, git_out
from gerrit_workflow_tools.core.git_state import resolve_working_branch
from gerrit_workflow_tools.core.stack import merge_base_with_target
from gerrit_workflow_tools.core.upstream_interactive import require_branch_upstream


logger = logging.getLogger(__name__)

# Re-export for tests and callers that imported from this module.
__all__ = [
    "main",
    "main_reword",
    "resolve_first_edit_attention_sha",
]


def _has_upstreams(cwd: Path, settings: Settings, branch: str | None) -> bool:
    if branch is not None and not require_branch_upstream(cwd, branch, settings=settings):
        return False
    return require_stack_upstream(cwd, settings)


def _resolve_in_stack(
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


def _print_cursor_status(cwd: Path, *, settings: Settings, branch: str | None, gerrit: GerritRest | None) -> int:
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


def _print_cursor_history(cwd: Path) -> int:
    events = read_cursor_history(cwd, limit=50)
    if not events:
        print("attention cursor history: (empty)")
        return 0
    for event in events:
        sha = f" {event.sha}" if event.sha else ""
        print(f"{event.ts}  {event.branch}  {event.action}  {event.change_id}{sha}")
    return 0


def _set_cursor_from_changeish(
    cwd: Path,
    changeish: str,
    *,
    settings: Settings,
    branch: str | None,
    gerrit: GerritRest | None,
) -> int:
    if branch is None:
        raise GitError("detached HEAD: cannot set attention cursor without a branch")
    target = _resolve_in_stack(cwd, changeish, settings=settings, branch=branch, gerrit=gerrit)
    change_id = change_id_for_stack_sha(cwd, target.sha, settings=settings, gerrit=gerrit)
    if not change_id:
        raise GitError("target commit has no Change-Id; cannot set attention cursor")
    write_branch_cursor(cwd, branch, change_id, action="set", sha=target.sha)
    print(f"attention cursor set to {change_id}")
    return 0


def _build_parser(*, prog: str, description: str, default_action: Literal["edit", "reword"]) -> argparse.ArgumentParser:
    """Build and return the parser for ``ger edit`` / ``ger reword``."""
    p = argparse.ArgumentParser(prog=prog, description=description)
    p.add_argument(
        "rev",
        nargs="?",
        metavar="REV",
        help="Git ref or Change-Id (I…); must be in the current stack. Optional with attention targeting.",
    )
    p.add_argument(
        "--first-attention-commit",
        action="store_true",
        help=(
            "Reset the attention walk to the oldest commit that needs edit attention "
            "(unresolved comments or failed build)."
        ),
    )
    p.add_argument(
        "-n",
        "--next",
        action="store_true",
        dest="next_attention",
        help=(
            "Edit the next edit-attention commit tip-ward of the attention cursor "
            "(or of REV when given), even if the cursor still needs attention."
        ),
    )
    p.add_argument(
        "--cursor",
        action="store_true",
        help="Print the attention cursor for the working branch and exit.",
    )
    p.add_argument(
        "--cursor-history",
        action="store_true",
        help="Print recent attention-cursor history and exit.",
    )
    p.add_argument(
        "--set-cursor",
        metavar="CHANGEISH",
        help="Set the attention cursor to CHANGEISH without starting a rebase.",
    )
    g = p.add_mutually_exclusive_group()
    p.set_defaults(action_override=None)
    if default_action == "edit":
        g.add_argument(
            "--reword", dest="action_override", action="store_const", const="reword", help="Reword commit message."
        )
        g.add_argument("--drop", dest="action_override", action="store_const", const="drop", help="Drop commit.")
    else:
        g.add_argument(
            "--edit",
            dest="action_override",
            action="store_const",
            const="edit",
            help="Stop at commit to amend (interactive rebase edit).",
        )
        g.add_argument("--drop", dest="action_override", action="store_const", const="drop", help="Drop commit.")
    add_verbose_and_debug_log_args(
        p,
        debug_log_help="Log git commands and rebase sequence editor steps to stderr.",
    )
    return p


def _build_parser_edit() -> argparse.ArgumentParser:
    """Build parser for ``ger edit`` (bash completion and tests)."""
    return _build_parser(
        prog="ger edit",
        description="Start an interactive rebase to edit, reword, or drop a commit in the current stack.",
        default_action="edit",
    )


def _build_parser_reword() -> argparse.ArgumentParser:
    """Build parser for ``ger reword`` (bash completion and tests)."""
    return _build_parser(
        prog="ger reword",
        description="Start an interactive rebase to reword a commit in the current stack (or use --edit / --drop).",
        default_action="reword",
    )


def _validate_targeting_args(p: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    inspect = sum(bool(x) for x in (args.cursor, args.cursor_history, args.set_cursor))
    if inspect > 1:
        p.error("use only one of --cursor, --cursor-history, --set-cursor")
    if inspect and (args.first_attention_commit or args.next_attention or args.rev):
        p.error("cannot combine cursor inspect/set with attention targeting or REV")
    if args.first_attention_commit and args.next_attention:
        p.error("cannot use --first-attention-commit with --next")
    if args.first_attention_commit and args.rev:
        p.error("cannot use REV with --first-attention-commit")


def _resolve_target_sha(
    cwd: Path,
    args: argparse.Namespace,
    *,
    settings: Settings,
    branch: str | None,
    gerrit: GerritRest | None,
) -> str:
    """Resolve the SHA to stop on for an interactive rebase."""
    rev_arg = args.rev
    after_change_id: str | None = None

    if args.next_attention and rev_arg is not None:
        after_target = _resolve_in_stack(cwd, rev_arg, settings=settings, branch=branch, gerrit=gerrit)
        after_change_id = change_id_for_stack_sha(cwd, after_target.sha, settings=settings, gerrit=gerrit)
        if not after_change_id:
            raise GitError("REV has no Change-Id; cannot use --next AFTER")
        rev_arg = None

    if args.first_attention_commit:
        mode: EditAttentionMode = "first"
    elif args.next_attention:
        mode = "next"
    elif rev_arg is None:
        mode = "resume"
    else:
        target_res = _resolve_in_stack(cwd, rev_arg, settings=settings, branch=branch, gerrit=gerrit)
        change_id = change_id_for_stack_sha(cwd, target_res.sha, settings=settings, gerrit=gerrit)
        if change_id and branch is not None:
            write_branch_cursor(cwd, branch, change_id, action="set", sha=target_res.sha)
        return target_res.sha

    target, notes = resolve_edit_attention_commit(
        cwd,
        settings=settings,
        mode=mode,
        branch=branch,
        after_change_id=after_change_id,
        gerrit=gerrit,
        update_cursor=True,
    )
    for note in notes:
        print(note, file=sys.stderr)
    return target.sha


def _run_interactive_stack_rebase(
    argv: list[str] | None,
    *,
    prog: str,
    description: str,
    default_action: Literal["edit", "reword"],
    gerrit: GerritRest | None = None,
) -> int:
    """Shared implementation for ``ger edit`` and ``ger reword``."""
    p = _build_parser(prog=prog, description=description, default_action=default_action)
    args = p.parse_args(argv)
    cwd, settings, _summary_highlighter = init_cli_runtime(args)
    _validate_targeting_args(p, args)

    branch = resolve_working_branch(cwd, settings=settings)

    if args.cursor_history:
        return _print_cursor_history(cwd)
    if not _has_upstreams(cwd, settings, branch):
        return int(ExitCode.ATTENTION)
    if args.cursor:
        return _print_cursor_status(cwd, settings=settings, branch=branch, gerrit=gerrit)
    if args.set_cursor:
        return _set_cursor_from_changeish(cwd, args.set_cursor, settings=settings, branch=branch, gerrit=gerrit)

    action = args.action_override or default_action
    logger.debug(
        "gedit cwd=%s rev_arg=%r first=%s next=%s action=%s",
        cwd,
        args.rev,
        args.first_attention_commit,
        args.next_attention,
        action,
    )

    full = _resolve_target_sha(cwd, args, settings=settings, branch=branch, gerrit=gerrit)
    rebase_fork, _, _ = merge_base_with_target(cwd, branch)
    short = git_out("rev-parse", "--short", full, cwd=cwd)

    env = os.environ.copy()
    env["GEDIT_FULL_SHA"] = full
    env["GEDIT_SHORT_SHA"] = short
    env["GEDIT_ACTION"] = action
    if args.debug_log:
        env["GEDIT_DEBUG_LOG"] = "1"
    env["GIT_SEQUENCE_EDITOR"] = f"{shlex.quote(sys.executable)} -m gerrit_workflow_tools.rebase_sequence_editor"

    logger.debug(
        "gedit starting interactive rebase onto rebase_fork=%s full=%s short=%s",
        rebase_fork[:8],
        full[:8],
        short,
    )
    cmd = ["git", "rebase", "-i", rebase_fork]
    logger.debug("run: %s (cwd=%s)", " ".join(cmd), cwd)
    r = subprocess.run(cmd, cwd=cwd, env=env, check=False)
    logger.debug("gedit rebase finished with return code %s", r.returncode)
    moved = default_action == "edit" and args.next_attention and r.returncode == 0
    if moved and settings.edit_next_show:
        from gerrit_workflow_tools.cli_ger import run_show

        show_code = run_show([full], gerrit=gerrit)
        if show_code not in (int(ExitCode.OK), int(ExitCode.ATTENTION)):
            return show_code
    return r.returncode


def main(argv: list[str] | None = None, *, gerrit: GerritRest | None = None) -> int:
    """CLI entry for ``ger edit``: interactive rebase to edit, reword, or drop a commit in the current stack."""
    return run_cli_command(
        lambda: _run_interactive_stack_rebase(
            argv,
            prog="ger edit",
            description="Start an interactive rebase to edit, reword, or drop a commit in the current stack.",
            default_action="edit",
            gerrit=gerrit,
        )
    )


def main_reword(argv: list[str] | None = None, *, gerrit: GerritRest | None = None) -> int:
    """CLI entry for ``ger reword``: interactive rebase with reword as the default action."""
    return run_cli_command(
        lambda: _run_interactive_stack_rebase(
            argv,
            prog="ger reword",
            description="Start an interactive rebase to reword a commit in the current stack (or use --edit / --drop).",
            default_action="reword",
            gerrit=gerrit,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
