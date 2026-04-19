from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from gerrit_workflow_tools.cli_common import (
    add_verbose_and_debug_log_args,
    configure_logging,
    cwd_from_env,
    handle_git_error,
)
from gerrit_workflow_tools.config import (
    branch_gerrit_reviewers,
    branch_gerrit_target,
    current_branch,
    set_branch_config,
)
from gerrit_workflow_tools.git_run import GitError

logger = logging.getLogger(__name__)


def _cmd_show(cwd: Path) -> int:
    b = current_branch(cwd)
    t = branch_gerrit_target(cwd, b)
    r = branch_gerrit_reviewers(cwd, b)
    print(f"Branch: {b}")
    print(f"Target branch: {t or '(not set)'}")
    print(f"Reviewers: {r or '(none)'}")
    return 0


def _cmd_init(ns: argparse.Namespace, cwd: Path) -> int:
    b = current_branch(cwd)
    if not ns.target:
        print("error: ger branch init requires --target <branch>", file=sys.stderr)
        return 1
    set_branch_config(
        cwd,
        b,
        gerrit_target=ns.target,
        gerrit_reviewers=ns.reviewers,
    )
    print(f"Configured branch {b!r}: target={ns.target}", file=sys.stderr)
    return 0


def _cmd_set_target(ns: argparse.Namespace, cwd: Path) -> int:
    b = current_branch(cwd)
    set_branch_config(cwd, b, gerrit_target=ns.value)
    return 0


def _cmd_set_reviewers(ns: argparse.Namespace, cwd: Path) -> int:
    b = current_branch(cwd)
    set_branch_config(cwd, b, gerrit_reviewers=ns.value)
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry for ``ger branch``: show or set branch-local Gerrit target and reviewers."""
    p = argparse.ArgumentParser(prog="ger branch")
    add_verbose_and_debug_log_args(
        p,
        debug_log_help="Log git commands and config writes to stderr.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("show", help="Show Gerrit metadata for the current branch.")

    ip = sub.add_parser("init", help="Set branch-local Gerrit targets (non-interactive).")
    ip.add_argument("--target", help="Gerrit review branch (e.g. main).")
    ip.add_argument(
        "--reviewers",
        default=None,
        metavar="REVIEWERS",
        help="Comma-separated Gerrit reviewer accounts.",
    )

    st = sub.add_parser("set-target", help="Set gerritTarget for the current branch.")
    st.add_argument("value", metavar="BRANCH")

    sr = sub.add_parser("set-reviewers", help="Set gerritReviewers for the current branch.")
    sr.add_argument("value", metavar="REVIEWERS")

    args = p.parse_args(argv)
    configure_logging(args.debug_log)
    cwd = cwd_from_env()
    logger.debug("gbranch cmd=%s cwd=%s", args.cmd, cwd)

    try:
        if args.cmd == "show":
            return _cmd_show(cwd)
        if args.cmd == "init":
            return _cmd_init(args, cwd)
        if args.cmd == "set-target":
            return _cmd_set_target(args, cwd)
        if args.cmd == "set-reviewers":
            return _cmd_set_reviewers(args, cwd)
    except GitError as e:
        return handle_git_error(e)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
