"""CLI for opening changed files from a selected commit."""

from __future__ import annotations

import argparse
import logging
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Literal

from gerrit_workflow_tools.cli_common import (
    ExitCode,
    add_verbose_and_debug_log_args,
    configure_logging,
    cwd_from_env,
    run_cli_command,
)
from gerrit_workflow_tools.core.annotated_stack import (
    branches_needing_upstream,
    load_annotated_stack,
    resolve_rev_range,
)
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.change_resolution import (
    ChangeResolutionError,
    format_resolution_note,
    resolve_stack_changeish,
)
from gerrit_workflow_tools.core.gerrit.rest import GerritApiError, GerritRest
from gerrit_workflow_tools.core.gerrit_change_status import first_commit_needing_edit_attention
from gerrit_workflow_tools.core.git_run import GitError, git_out
from gerrit_workflow_tools.core.git_state import resolve_working_branch
from gerrit_workflow_tools.core.stack import merge_base_with_target
from gerrit_workflow_tools.core.upstream_interactive import require_branch_upstream


logger = logging.getLogger(__name__)


def resolve_first_edit_attention_sha(cwd: Path, *, settings: Settings, gerrit: GerritRest | None = None) -> str:
    """Return full SHA of the oldest commit with unresolved comments or a failed build."""
    rev_range = resolve_rev_range(cwd, None, settings=settings)
    for branch in branches_needing_upstream(cwd, rev_range, settings=settings):
        if not require_branch_upstream(cwd, branch, settings=settings):
            raise GitError("upstream not configured")
    try:
        stack_view = load_annotated_stack(cwd, rev_range, settings=settings, gerrit=gerrit)
    except (ValueError, ChangeResolutionError, GerritApiError) as e:
        raise GitError(f"could not load stack commits: {e}") from e
    if not stack_view.commits:
        raise GitError("no commits in stack")
    target = first_commit_needing_edit_attention(stack_view.commits)
    if target is None:
        raise GitError("no commit needs edit attention (unresolved comments or build failed)")
    return target.sha


def _build_parser(*, prog: str, description: str, default_action: Literal["edit", "reword"]) -> argparse.ArgumentParser:
    """Build and return the parser for ``ger edit`` / ``ger reword``."""
    p = argparse.ArgumentParser(prog=prog, description=description)
    p.add_argument(
        "rev",
        nargs="?",
        metavar="REV",
        help="Git ref or Change-Id (I…); must be in the current stack.",
    )
    p.add_argument(
        "--first-attention-commit",
        action="store_true",
        help=(
            "Edit the oldest commit that needs attention: unresolved Gerrit comments "
            "or failed build (same detection as ``ger log``)."
        ),
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
    """Build parser for ``ger edit``."""
    return _build_parser(
        prog="ger edit",
        description="Start an interactive rebase to edit, reword, or drop a commit in the current stack.",
        default_action="edit",
    )


def _build_parser_reword() -> argparse.ArgumentParser:
    """Build parser for ``ger reword``."""
    return _build_parser(
        prog="ger reword",
        description="Start an interactive rebase to reword a commit in the current stack (or use --edit / --drop).",
        default_action="reword",
    )


# pylint: disable=too-many-locals
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
    configure_logging(args.debug_log)
    cwd = cwd_from_env()
    settings = Settings.from_cwd(cwd)

    if args.first_attention_commit and args.rev:
        p.error("cannot use REV with --first-attention-commit")
    if not args.first_attention_commit and not args.rev:
        p.error("the following arguments are required: REV (or use --first-attention-commit)")

    action = args.action_override or default_action
    rev_arg = args.rev
    logger.debug(
        "gedit cwd=%s rev_arg=%r first_attention=%s action=%s", cwd, rev_arg, args.first_attention_commit, action
    )

    branch = resolve_working_branch(cwd, settings=settings)
    if branch is not None and not require_branch_upstream(cwd, branch, settings=settings):
        return int(ExitCode.ATTENTION)
    if args.first_attention_commit:
        full = resolve_first_edit_attention_sha(cwd, settings=settings, gerrit=gerrit)
        rev_arg = git_out("rev-parse", "--short", full, cwd=cwd)
    else:
        assert rev_arg is not None
        target = resolve_stack_changeish(
            cwd,
            rev_arg,
            settings=settings,
            gerrit=gerrit,
            branch=branch,
            require_in_stack=True,
        )
        full = target.sha
        if target.resolution is not None:
            note = format_resolution_note(target.resolution)
            if note:
                print(note, file=sys.stderr)
    rebase_fork, _, _ = merge_base_with_target(cwd, branch)
    short = git_out("rev-parse", "--short", full, cwd=cwd)

    env = os.environ.copy()
    env["GEDIT_FULL_SHA"] = full
    env["GEDIT_SHORT_SHA"] = short
    env["GEDIT_ACTION"] = action
    if args.debug_log:
        env["GEDIT_DEBUG_LOG"] = "1"
    # Quoted for paths with spaces (typical when Python is not from a venv).
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
