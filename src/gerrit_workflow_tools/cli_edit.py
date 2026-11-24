"""CLI for ``ger edit`` / ``ger reword``: interactive rebase that edits, rewords, or drops one stack commit."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Literal

from gerrit_workflow_tools.cli_args import (
    add_message_args,
    add_verbose_and_debug_log_args,
    changeish_help,
    read_message,
)
from gerrit_workflow_tools.cli_attention import (
    print_cursor_history,
    print_cursor_status,
    require_stack_upstream,
    resolve_in_stack,
    set_cursor_from_changeish,
)
from gerrit_workflow_tools.cli_common import ExitCode, init_cli_runtime, run_cli_command
from gerrit_workflow_tools.cli_terminal import stdin_is_terminal
from gerrit_workflow_tools.core.attention_cursor import (
    CursorAction,
    EditAttentionMode,
    load_branch_cursor,
    write_branch_cursor,
)
from gerrit_workflow_tools.core.edit_attention_walk import (
    change_id_for_stack_sha,
    resolve_edit_attention_commit,
    resolve_first_edit_attention_sha,
    stack_log_commit,
)
from gerrit_workflow_tools.core.gerrit.rest import GerritRest
from gerrit_workflow_tools.core.gerrit_change_status import LogCommit
from gerrit_workflow_tools.core.git_run import GitError, git_out
from gerrit_workflow_tools.core.rebase_next_step import attention_stop_hint
from gerrit_workflow_tools.core.rebase_state import RebaseAction, rebase_in_progress, rebase_refusal, unmerged_paths
from gerrit_workflow_tools.core.rewrite_refusal import closed_change_refusal
from gerrit_workflow_tools.core.run_context import RunContext
from gerrit_workflow_tools.core.stack import merge_base_with_target
from gerrit_workflow_tools.core.usage import UsageError
from gerrit_workflow_tools.rebase_run import go_on_with_rebase, require_history_rewrite, run_interactive_rebase
from gerrit_workflow_tools.render.status_fmt import attention_text
from gerrit_workflow_tools.reword_run import message_from_editor, reword
from gerrit_workflow_tools.upstream_interactive import require_branch_upstream


logger = logging.getLogger(__name__)

# Re-export for tests and callers that imported from this module.
__all__ = [
    "main",
    "main_reword",
    "resolve_first_edit_attention_sha",
]


def _has_upstreams(run: RunContext, branch: str | None) -> bool:
    if branch is not None and not require_branch_upstream(run.cwd, branch, settings=run.settings):
        return False
    return require_stack_upstream(run)


def _build_parser(*, prog: str, description: str, default_action: Literal["edit", "reword"]) -> argparse.ArgumentParser:
    """Build and return the parser for ``ger edit`` / ``ger reword``."""
    p = argparse.ArgumentParser(prog=prog, description=description)
    p.add_argument(
        "rev",
        nargs="?",
        metavar="REV",
        help=changeish_help("The commit, in the current stack; optional with attention targeting"),
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
    add_message_args(
        p
    ).description = "The new message for a reword, without an editor; a message without a Change-Id keeps the commit's."
    add_verbose_and_debug_log_args(
        p,
        debug_log_help="Log git commands and rebase sequence editor steps to stderr.",
    )
    return p


_REWORD_DESCRIPTION = (
    "Reword a commit in the current stack: only messages change, no rebase, so uncommitted changes stay "
    "and nothing can conflict. The message comes from -m / -F, else your editor (or use --edit / --drop)."
)


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
        description=_REWORD_DESCRIPTION,
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
    run: RunContext,
    args: argparse.Namespace,
    *,
    branch: str | None,
    action: str,
) -> tuple[str, str | None, CursorAction, LogCommit | None]:
    """Resolve the SHA to stop on, plus the Change-Id and action for the cursor move it earns.

    Last, the annotated stack's row for that commit: the Gerrit status the closed-change check
    needs, and the attention a stop names, with no request of their own.
    """
    rev_arg = args.rev
    after_change_id: str | None = None

    if args.next_attention and rev_arg is not None:
        after_target = resolve_in_stack(run, rev_arg, branch=branch)
        after_change_id = change_id_for_stack_sha(run, after_target.sha)
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
        target_res = resolve_in_stack(run, rev_arg, branch=branch)
        row = stack_log_commit(run, target_res.sha)
        return target_res.sha, row.change_id if row else None, "set", row

    target, notes = resolve_edit_attention_commit(
        run, mode=mode, branch=branch, after_change_id=after_change_id, update_cursor=False
    )
    for note in notes:
        print(note, file=sys.stderr)
    # Said before the editor opens: the walk chose this commit, not the user.
    why = attention_text(target) or "needs edit attention"
    print(f"{_VERBS[action]} {target.short_sha} {target.summary} ({_WALK[mode]}: {why})", file=sys.stderr)
    return target.sha, target.change_id, mode, target


_WALK_ON = "ger edit -n"
_VERBS = {"edit": "editing", "reword": "rewording", "drop": "dropping"}
_WALK = {"first": "oldest needing attention", "next": "next needing attention", "resume": "attention walk"}


def _continue_stopped_rebase(run: RunContext) -> int:
    """Finish the rebase a previous edit left stopped.

    Returns 0 when that rebase is done. Git's own code when ``--continue`` fails; refused first, as
    ``ger rebase --continue`` is, over unresolved conflicts or unstaged changes.
    ``ExitCode.GIT`` when git exited 0 but a rebase is still underway, so a new one
    cannot start.
    """
    cwd = run.cwd
    print("continuing the rebase", file=sys.stderr)
    code = go_on_with_rebase(run, RebaseAction.CONTINUE, walk_on=_WALK_ON)  # says `rebase finished: …`
    if not rebase_in_progress(cwd):
        return code
    if code == 0:
        print("rebase is still in progress; `ger edit -n` to continue it", file=sys.stderr)
        return int(ExitCode.GIT)
    if not unmerged_paths(cwd):  # a conflict already got its next step
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
    run = init_cli_runtime(args, gerrit=gerrit)
    cwd, settings = run.cwd, run.settings
    _validate_targeting_args(p, args)

    branch = run.repo.working_branch

    if args.cursor_history:
        return print_cursor_history(cwd)
    if args.cursor and load_branch_cursor(settings, branch or "HEAD") is None:
        return print_cursor_status(run, branch=branch)
    if not _has_upstreams(run, branch):
        return int(ExitCode.REFUSED)
    if args.cursor:
        return print_cursor_status(run, branch=branch)
    if args.set_cursor:
        return set_cursor_from_changeish(run, args.set_cursor, branch=branch)

    action = args.action_override or default_action
    given = args.message is not None or args.file is not None
    if given and action != "reword":
        p.error("-m and -F give a reword its message: use them with ger reword, or ger edit --reword")
    if args.message is not None and args.file is not None:
        p.error("give the message with one of -m or -F")
    logger.debug(
        "ger edit cwd=%s rev_arg=%r first=%s next=%s action=%s",
        cwd,
        args.rev,
        args.first_attention_commit,
        args.next_attention,
        action,
    )

    if rebase_in_progress(cwd) and default_action == "edit" and args.next_attention:
        continued = _continue_stopped_rebase(run)
        if continued != 0:
            return continued
    if action != "reword":  # a reword touches no tree: uncommitted changes are no obstacle
        require_history_rewrite(cwd)
    elif refusal := rebase_refusal(cwd):
        raise UsageError(refusal)
    full, cursor_change_id, cursor_action, row = _resolve_target(run, args, branch=branch, action=action)
    short = git_out("rev-parse", "--short", full, cwd=cwd)
    # Dropping a change Gerrit closed is just what to do with it; editing or rewording it is not.
    if action != "drop" and (
        refusal := closed_change_refusal(
            run.gerrit, cursor_change_id, commit=short, statuses=(row.change_status if row else None,)
        )
    ):
        raise UsageError(refusal)
    if action == "reword":
        if not given and not (stdin_is_terminal() or os.environ.get("GIT_EDITOR")):
            raise UsageError(
                "no terminal to edit the message in: give it with -m MSG or -F FILE ('-': stdin), "
                "or set GIT_EDITOR to an editor that runs without one"
            )
        message = read_message(args.message, args.file) if given else message_from_editor(run, full)
        code = reword(run, full, message)
        if code == 0 and cursor_change_id and branch is not None:
            write_branch_cursor(cwd, branch, cursor_change_id, action=cursor_action, sha=full)
        return code
    rebase_fork, _, _ = merge_base_with_target(cwd, branch)

    env = {"GEDIT_FULL_SHA": full, "GEDIT_SHORT_SHA": short, "GEDIT_ACTION": action}
    if args.debug_log:
        env["GEDIT_DEBUG_LOG"] = "1"
    logger.debug("ger edit rebasing onto %s to stop at %s", rebase_fork[:8], short)
    returncode = run_interactive_rebase(
        run,
        rebase_fork,
        sequence_editor="rebase_sequence_editor",
        env=env,
        walk_on=_WALK_ON if cursor_change_id and cursor_action != "set" else None,
        undo="reset" if action == "drop" else "cherry-pick",  # a drop changed nothing else: reset takes it back
    )
    # A non-zero exit with a rebase underway is a conflict: the walk did reach this commit.
    if cursor_change_id and branch is not None and (returncode == 0 or rebase_in_progress(cwd)):
        write_branch_cursor(cwd, branch, cursor_change_id, action=cursor_action, sha=full)
    if returncode == 0 and rebase_in_progress(cwd) and row and (hint := attention_stop_hint(row.attention_reasons)):
        print(hint, file=sys.stderr)
    walked = args.next_attention or args.first_attention_commit
    moved = default_action == "edit" and walked and returncode == 0
    if moved and settings.show_after_edit:
        from gerrit_workflow_tools.cli_ger import run_show

        show_code = run_show([full], gerrit=gerrit)
        if show_code not in (int(ExitCode.OK), int(ExitCode.REFUSED)):
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
            description=_REWORD_DESCRIPTION,
            default_action="reword",
            gerrit=gerrit,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
