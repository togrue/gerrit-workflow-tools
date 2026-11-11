"""CLI for ``ger edit`` / ``ger reword``: interactive rebase that edits, rewords, or drops one stack commit."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Literal

from gerrit_workflow_tools.cli_attention import (
    print_cursor_history,
    print_cursor_status,
    require_stack_upstream,
    resolve_in_stack,
    set_cursor_from_changeish,
)
from gerrit_workflow_tools.cli_common import (
    ExitCode,
    add_verbose_and_debug_log_args,
    continue_rebase,
    init_cli_runtime,
    require_history_rewrite,
    run_cli_command,
    run_interactive_rebase,
)
from gerrit_workflow_tools.core.attention_cursor import (
    CursorAction,
    EditAttentionMode,
    load_branch_cursor,
    write_branch_cursor,
)
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.edit_attention_walk import (
    change_id_for_stack_sha,
    resolve_edit_attention_commit,
    resolve_first_edit_attention_sha,
)
from gerrit_workflow_tools.core.gerrit.rest import GerritRest
from gerrit_workflow_tools.core.git_run import GitError, git_out
from gerrit_workflow_tools.core.git_state import rebase_in_progress, resolve_working_branch
from gerrit_workflow_tools.core.stack import get_stack_snapshot, merge_base_with_target
from gerrit_workflow_tools.core.upstream_interactive import require_branch_upstream


logger = logging.getLogger(__name__)

# Re-export for tests and callers that imported from this module.
__all__ = [
    "main",
    "main_reword",
    "resolve_first_edit_attention_sha",
]


def _stack_change_ids(cwd: Path, branch: str | None) -> set[str]:
    return {commit.change_id for commit in get_stack_snapshot(cwd, branch).commits if commit.change_id}


def _has_upstreams(cwd: Path, settings: Settings, branch: str | None) -> bool:
    if branch is not None and not require_branch_upstream(cwd, branch, settings=settings):
        return False
    return require_stack_upstream(cwd, settings)


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
            "(unresolved comments or failed build; a vote alone does not count)."
        ),
    )
    p.add_argument(
        "-n",
        "--next",
        action="store_true",
        dest="next_attention",
        help=(
            "Edit the next edit-attention commit tip-ward of the attention cursor "
            "(or of REV when given), even if the cursor still needs attention. "
            "An in-progress rebase is continued first."
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


def _resolve_target(
    cwd: Path,
    args: argparse.Namespace,
    *,
    settings: Settings,
    branch: str | None,
    gerrit: GerritRest | None,
) -> tuple[str, str | None, CursorAction]:
    """Resolve the SHA to stop on, plus the Change-Id and action for the cursor move it earns."""
    rev_arg = args.rev
    after_change_id: str | None = None

    if args.next_attention and rev_arg is not None:
        after_target = resolve_in_stack(cwd, rev_arg, settings=settings, branch=branch, gerrit=gerrit)
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
        target_res = resolve_in_stack(cwd, rev_arg, settings=settings, branch=branch, gerrit=gerrit)
        return target_res.sha, change_id_for_stack_sha(cwd, target_res.sha, settings=settings, gerrit=gerrit), "set"

    target, notes = resolve_edit_attention_commit(
        cwd,
        settings=settings,
        mode=mode,
        branch=branch,
        after_change_id=after_change_id,
        gerrit=gerrit,
        update_cursor=False,
    )
    for note in notes:
        print(note, file=sys.stderr)
    return target.sha, target.change_id, mode


def _continue_stopped_rebase(cwd: Path) -> int:
    """Finish the rebase a previous edit left stopped.

    Returns 0 when that rebase is done. Git's own code when ``--continue`` fails.
    ``ExitCode.GIT`` when git exited 0 but a rebase is still underway, so a new one
    cannot start.
    """
    print("continuing the rebase", file=sys.stderr)
    code = continue_rebase(cwd)
    if not rebase_in_progress(cwd):
        return code
    if code == 0:
        print("rebase is still in progress; `ger edit -n` to continue it", file=sys.stderr)
        return int(ExitCode.GIT)
    print("`ger edit -n` again once the rebase can continue", file=sys.stderr)
    return code


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
        return print_cursor_history(cwd)
    if args.cursor and load_branch_cursor(settings, branch or "HEAD") is None:
        return print_cursor_status(cwd, settings=settings, branch=branch, gerrit=gerrit)
    if not _has_upstreams(cwd, settings, branch):
        return int(ExitCode.ATTENTION)
    if args.cursor:
        return print_cursor_status(cwd, settings=settings, branch=branch, gerrit=gerrit)
    if args.set_cursor:
        return set_cursor_from_changeish(cwd, args.set_cursor, settings=settings, branch=branch, gerrit=gerrit)

    action = args.action_override or default_action
    logger.debug(
        "gedit cwd=%s rev_arg=%r first=%s next=%s action=%s",
        cwd,
        args.rev,
        args.first_attention_commit,
        args.next_attention,
        action,
    )

    if rebase_in_progress(cwd) and default_action == "edit" and args.next_attention:
        continued = _continue_stopped_rebase(cwd)
        if continued != 0:
            return continued
    require_history_rewrite(cwd)
    full, cursor_change_id, cursor_action = _resolve_target(cwd, args, settings=settings, branch=branch, gerrit=gerrit)
    rebase_fork, _, _ = merge_base_with_target(cwd, branch)
    short = git_out("rev-parse", "--short", full, cwd=cwd)

    change_ids_before = _stack_change_ids(cwd, branch) if action == "reword" else set()
    env = {"GEDIT_FULL_SHA": full, "GEDIT_SHORT_SHA": short, "GEDIT_ACTION": action}
    if args.debug_log:
        env["GEDIT_DEBUG_LOG"] = "1"
    logger.debug("gedit rebasing onto %s to stop at %s", rebase_fork[:8], short)
    returncode = run_interactive_rebase(cwd, rebase_fork, sequence_editor="rebase_sequence_editor", env=env)
    # A non-zero exit with a rebase underway is a conflict: the walk did reach this commit.
    if cursor_change_id and branch is not None and (returncode == 0 or rebase_in_progress(cwd)):
        write_branch_cursor(cwd, branch, cursor_change_id, action=cursor_action, sha=full)
    if returncode == 0 and rebase_in_progress(cwd):
        subject = git_out("log", "-1", "--format=%s", full, cwd=cwd)
        print(f"stopped at {short} {subject}", file=sys.stderr)
        print("next: amend it, then `ger edit -n`", file=sys.stderr)
    elif returncode == 0:
        for lost in sorted(change_ids_before - _stack_change_ids(cwd, branch)):
            print(f"warning: Change-Id {lost} left the stack; the next push creates a new change", file=sys.stderr)
    moved = default_action == "edit" and args.next_attention and returncode == 0
    if moved and settings.edit_next_show:
        from gerrit_workflow_tools.cli_ger import run_show

        show_code = run_show([full], gerrit=gerrit)
        if show_code not in (int(ExitCode.OK), int(ExitCode.ATTENTION)):
            return show_code
    return returncode


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
