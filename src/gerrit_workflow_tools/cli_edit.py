from __future__ import annotations

import argparse
import logging
import os
import shlex
import subprocess
import sys
from typing import Literal

from gerrit_workflow_tools.cli_common import (
    add_verbose_and_debug_log_args,
    configure_logging,
    cwd_from_env,
    handle_git_error,
)
from gerrit_workflow_tools.git_run import GitError, git_out
from gerrit_workflow_tools.stack import (
    commit_in_stack,
    merge_base_with_target,
    resolve_stack_commit,
)

logger = logging.getLogger(__name__)


def _run_interactive_stack_rebase(
    argv: list[str] | None,
    *,
    prog: str,
    description: str,
    default_action: Literal["edit", "reword"],
) -> int:
    """Shared implementation for ``ger edit`` and ``ger reword``."""
    p = argparse.ArgumentParser(prog=prog, description=description)
    p.add_argument(
        "rev",
        metavar="REV",
        help="Git ref or Change-Id (I…); must be in the current stack.",
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
    args = p.parse_args(argv)
    configure_logging(args.debug_log)
    cwd = cwd_from_env()

    action = args.action_override or default_action
    logger.debug("gedit cwd=%s rev_arg=%r action=%s", cwd, args.rev, action)

    try:
        full = resolve_stack_commit(cwd, args.rev.strip())
        if not commit_in_stack(cwd, full):
            raise GitError(f"commit {args.rev} is not in the current local stack")
        rebase_fork, _, _ = merge_base_with_target(cwd)
        short = git_out("rev-parse", "--short", full, cwd=cwd)
    except GitError as e:
        return handle_git_error(e)

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
    r = subprocess.run(cmd, cwd=cwd, env=env)
    logger.debug("gedit rebase finished with return code %s", r.returncode)
    return r.returncode


def main(argv: list[str] | None = None) -> int:
    """CLI entry for ``ger edit``: interactive rebase to edit, reword, or drop a commit in the current stack."""
    return _run_interactive_stack_rebase(
        argv,
        prog="ger edit",
        description="Start an interactive rebase to edit, reword, or drop a commit in the current stack.",
        default_action="edit",
    )


def main_reword(argv: list[str] | None = None) -> int:
    """CLI entry for ``ger reword``: interactive rebase with reword as the default action."""
    return _run_interactive_stack_rebase(
        argv,
        prog="ger reword",
        description="Start an interactive rebase to reword a commit in the current stack (or use --edit / --drop).",
        default_action="reword",
    )


if __name__ == "__main__":
    raise SystemExit(main())
