from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from gerrit_workflow_tools.cli_common import configure_logging, cwd_from_env, handle_git_error
from gerrit_workflow_tools.config import (
    branch_gerrit_push_mode,
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
    m = branch_gerrit_push_mode(cwd, b)
    print(f"Branch: {b}")
    print(f"Target branch: {t or '(not set)'}")
    print(f"Reviewers: {r or '(none)'}")
    print(f"Push mode: {m or '(default)'}")
    return 0


def _cmd_init(ns: argparse.Namespace, cwd: Path) -> int:
    b = current_branch(cwd)
    if not ns.target:
        print("error: git gbranch init requires --target <branch>", file=sys.stderr)
        return 1
    set_branch_config(
        cwd,
        b,
        gerrit_target=ns.target,
        gerrit_reviewers=ns.reviewers,
        gerrit_push_mode=ns.push_mode or "ready",
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


def _cmd_set_push_mode(ns: argparse.Namespace, cwd: Path) -> int:
    b = current_branch(cwd)
    set_branch_config(cwd, b, gerrit_push_mode=ns.value)
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry for ``git gbranch``: show or set branch-local Gerrit target, reviewers, and push mode."""
    p = argparse.ArgumentParser(prog="git gbranch")
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="log git commands and config writes to stderr",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("show", help="show Gerrit metadata for current branch")

    ip = sub.add_parser("init", help="set branch-local Gerrit targets (non-interactive)")
    ip.add_argument("--target", help="Gerrit review branch (e.g. main)")
    ip.add_argument("--reviewers", default=None, help="comma-separated reviewers")
    ip.add_argument("--push-mode", default="ready", dest="push_mode")

    st = sub.add_parser("set-target", help="set gerritTarget for current branch")
    st.add_argument("value", metavar="BRANCH")

    sr = sub.add_parser("set-reviewers", help="set gerritReviewers for current branch")
    sr.add_argument("value", metavar="LIST")

    sm = sub.add_parser("set-push-mode", help="set gerritPushMode for current branch")
    sm.add_argument("value", metavar="MODE")

    args = p.parse_args(argv)
    configure_logging(args.verbose)
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
        if args.cmd == "set-push-mode":
            return _cmd_set_push_mode(args, cwd)
    except GitError as e:
        return handle_git_error(e)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
