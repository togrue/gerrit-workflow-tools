from __future__ import annotations

import argparse
import logging
import subprocess
import sys

from gerrit_workflow_tools.change_id import classify_issues
from gerrit_workflow_tools.cli_common import configure_logging, cwd_from_env, handle_git_error
from gerrit_workflow_tools.config import (
    branch_gerrit_target,
    gerrit_remote,
    set_branch_config,
)
from gerrit_workflow_tools.git_run import GitError, git_out
from gerrit_workflow_tools.ready_calc import change_id_rows_for_range, compute_ready
from gerrit_workflow_tools.stack import merge_base_with_target

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    """CLI entry for ``git gpush``: compute ready range, validate Change-Ids, and push to Gerrit."""
    p = argparse.ArgumentParser(prog="git gpush")
    p.add_argument(
        "-i", action="store_true", help="interactive (not implemented in CLI stub)"
    )
    p.add_argument(
        "--dry-run", action="store_true", help="print actions only, do not push"
    )
    p.add_argument(
        "--all",
        action="store_true",
        dest="all_",
        help="push full stack (ignore stop patterns)",
    )
    p.add_argument("--until", metavar="REV", help="push only through this commit")
    p.add_argument(
        "--target", metavar="BRANCH", help="Gerrit target branch for this push"
    )
    p.add_argument(
        "--save-target", action="store_true", help="store --target for this branch"
    )
    p.add_argument(
        "--force-boundary",
        action="store_true",
        help="ignore ready boundary (like --all)",
    )
    p.add_argument("--ignore-pattern", action="append", default=[], metavar="REGEX")
    p.add_argument("--no-config-patterns", action="store_true")
    p.add_argument(
        "--reviewer", action="append", default=[], help="(reserved) Gerrit reviewers"
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="log git commands and push steps to stderr",
    )
    args = p.parse_args(argv)
    configure_logging(args.verbose)
    cwd = cwd_from_env()
    logger.debug(
        "gpush cwd=%s dry_run=%s all=%s until=%s target=%s save_target=%s",
        cwd,
        args.dry_run,
        args.all_,
        args.until,
        args.target,
        args.save_target,
    )

    if args.i:
        print(
            "error: interactive mode is not implemented; use git gbranch init and git gpush",
            file=sys.stderr,
        )
        return 1

    try:
        b = git_out("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd)
        if args.target and args.save_target:
            set_branch_config(cwd, b, gerrit_target=args.target)

        target = args.target or branch_gerrit_target(cwd, b)
        if not target:
            raise GitError(
                "No Gerrit target: run `git gbranch init --target <branch>` or `git gpush --target <branch>`."
            )

        r = compute_ready(
            cwd,
            branch=None,
            all_commits=args.all_ or args.force_boundary,
            no_config_patterns=args.no_config_patterns,
            ignore_patterns=args.ignore_pattern or None,
            until=args.until,
        )
        logger.debug(
            "gpush ready tip=%s range=%s boundary=%s",
            r.push_tip_sha,
            r.push_range,
            r.boundary_reason,
        )

        mb, _, _ = merge_base_with_target(cwd)
        rows = change_id_rows_for_range(cwd, mb)
        items = [(a, b, c) for a, b, c in rows]
        _, cid_exit = classify_issues(items, strict=True)
        logger.debug("gpush change_id check exit=%d commits=%d", cid_exit, len(items))
        if cid_exit >= 2:
            print(
                "error: Change-Id check failed; fix with git gcid --check-duplicates",
                file=sys.stderr,
            )
            return 2

        remote = gerrit_remote(cwd)
        tip = r.push_tip_sha
        if not tip:
            print("error: nothing to push (empty ready prefix)", file=sys.stderr)
            return 1

        refspec = f"{tip}:refs/for/{target}"
        cmd = ["git", "push", remote, refspec]

        print("Summary")
        print(f"  branch:       {b}")
        print(f"  target:       {target}")
        print(f"  remote:       {remote}")
        print(f"  push tip:     {tip}")
        print(f"  ready reason: {r.boundary_reason}")
        print(f"  push range:   {r.push_range or '(n/a)'}")
        print()
        print(" ".join(cmd))
        if args.dry_run:
            print("[dry-run] not executing push", file=sys.stderr)
            return 0

        logger.debug("gpush executing: %s", " ".join(cmd))
        proc = subprocess.run(cmd, cwd=cwd)
        logger.debug("gpush push finished with return code %s", proc.returncode)
        return proc.returncode
    except GitError as e:
        return handle_git_error(e)


if __name__ == "__main__":
    raise SystemExit(main())
