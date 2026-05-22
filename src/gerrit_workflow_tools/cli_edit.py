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
    add_verbose_and_debug_log_args,
    configure_logging,
    cwd_from_env,
    handle_git_error,
)
from gerrit_workflow_tools.cli_log import (
    load_annotated_commits,
    resolve_rev_range,
    rev_range_needs_upstream_resolution,
)
from gerrit_workflow_tools.core.config import resolve_working_branch
from gerrit_workflow_tools.core.gerrit_change_status import first_commit_needing_edit_attention
from gerrit_workflow_tools.core.git_run import GitError, git_out
from gerrit_workflow_tools.core.stack import (
    commit_in_stack,
    merge_base_with_target,
    resolve_stack_commit,
)
from gerrit_workflow_tools.core.upstream_interactive import branch_has_upstream, ensure_branch_upstream_interactive

logger = logging.getLogger(__name__)


def resolve_first_edit_attention_sha(cwd: Path) -> str:
    """Return full SHA of the oldest commit with unresolved comments or a failed build."""
    rev_range, rev_range_exit = resolve_rev_range(cwd, None)
    if rev_range_exit is not None:
        raise GitError("could not resolve commit range for stack")
    assert rev_range is not None
    for branch in rev_range_needs_upstream_resolution(cwd, rev_range):
        if branch_has_upstream(cwd, branch):
            continue
        if not ensure_branch_upstream_interactive(cwd, branch) and sys.stdin.isatty():
            raise GitError("upstream not configured")
    commits, load_exit = load_annotated_commits(cwd, rev_range)
    if commits is None:
        if load_exit == 0:
            raise GitError("no commits in stack")
        raise GitError("could not load stack commits")
    target = first_commit_needing_edit_attention(commits)
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
) -> int:
    """Shared implementation for ``ger edit`` and ``ger reword``."""
    p = _build_parser(prog=prog, description=description, default_action=default_action)
    args = p.parse_args(argv)
    configure_logging(args.debug_log)
    cwd = cwd_from_env()

    if args.first_attention_commit and args.rev:
        p.error("cannot use REV with --first-attention-commit")
    if not args.first_attention_commit and not args.rev:
        p.error("the following arguments are required: REV (or use --first-attention-commit)")

    action = args.action_override or default_action
    rev_arg = args.rev
    logger.debug(
        "gedit cwd=%s rev_arg=%r first_attention=%s action=%s", cwd, rev_arg, args.first_attention_commit, action
    )

    try:
        branch = resolve_working_branch(cwd)
        if (
            branch is not None
            and not branch_has_upstream(cwd, branch)
            and not ensure_branch_upstream_interactive(cwd, branch)
            and sys.stdin.isatty()
        ):
            return 1
        if args.first_attention_commit:
            full = resolve_first_edit_attention_sha(cwd)
            rev_arg = git_out("rev-parse", "--short", full, cwd=cwd)
        else:
            assert rev_arg is not None
            full = resolve_stack_commit(cwd, rev_arg.strip(), branch=branch)
        if not commit_in_stack(cwd, full, branch=branch):
            raise GitError(f"commit {rev_arg} is not in the current local stack")
        rebase_fork, _, _ = merge_base_with_target(cwd, branch)
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
    r = subprocess.run(cmd, cwd=cwd, env=env, check=False)
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
