from __future__ import annotations

import argparse
import logging
import os
import shlex
import subprocess
import sys

from gerrit_workflow_tools.cli_common import configure_logging, cwd_from_env, handle_git_error
from gerrit_workflow_tools.git_run import GitError, git_out
from gerrit_workflow_tools.stack import (
    commit_in_stack,
    merge_base_with_target,
    resolve_stack_commit,
)

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    """CLI entry for ``git gedit``: interactive rebase to edit, reword, or drop a commit in the current stack."""
    p = argparse.ArgumentParser(prog="git gedit")
    p.add_argument(
        "rev",
        metavar="REV",
        help="Git ref or Change-Id (I…); must be in the current stack.",
    )
    g = p.add_mutually_exclusive_group()
    g.add_argument("--reword", action="store_true", help="Reword commit message.")
    g.add_argument("--drop", action="store_true", help="Drop commit.")
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Log git commands and rebase sequence editor steps to stderr.",
    )
    args = p.parse_args(argv)
    configure_logging(args.verbose)
    cwd = cwd_from_env()

    action = "reword" if args.reword else "drop" if args.drop else "edit"
    logger.debug("gedit cwd=%s rev_arg=%r action=%s", cwd, args.rev, action)

    try:
        full = resolve_stack_commit(cwd, args.rev.strip())
        if not commit_in_stack(cwd, full):
            raise GitError(f"commit {args.rev} is not in the current local stack")
        mb, _, _ = merge_base_with_target(cwd)
        short = git_out("rev-parse", "--short", full, cwd=cwd)
    except GitError as e:
        return handle_git_error(e)

    env = os.environ.copy()
    env["GEDIT_FULL_SHA"] = full
    env["GEDIT_SHORT_SHA"] = short
    env["GEDIT_ACTION"] = action
    if args.verbose:
        env["GEDIT_VERBOSE"] = "1"
    # Quoted for paths with spaces (typical when Python is not from a venv).
    env["GIT_SEQUENCE_EDITOR"] = f"{shlex.quote(sys.executable)} -m gerrit_workflow_tools.rebase_sequence_editor"

    logger.debug(
        "gedit starting interactive rebase onto merge_base=%s full=%s short=%s",
        mb[:8],
        full[:8],
        short,
    )
    r = subprocess.run(
        ["git", "rebase", "-i", mb],
        cwd=cwd,
        env=env,
    )
    logger.debug("gedit rebase finished with return code %s", r.returncode)
    return r.returncode


if __name__ == "__main__":
    raise SystemExit(main())
