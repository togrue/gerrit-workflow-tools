from __future__ import annotations

import argparse
import json
import logging

from gerrit_workflow_tools.cli_common import (
    configure_logging,
    cwd_from_env,
    handle_git_error,
)
from gerrit_workflow_tools.git_run import GitError
from gerrit_workflow_tools.stack import build_stack

logger = logging.getLogger(__name__)


def _symbol(state: str) -> str:
    if state == "ready":
        return "✓"
    if state.startswith("blocked"):
        return "!"
    return "x"


def main(argv: list[str] | None = None) -> int:
    """CLI entry for ``git gstack``: list commits from merge-base to HEAD with optional Change-Id and ready state."""
    p = argparse.ArgumentParser(prog="git gstack")
    p.add_argument("--json", action="store_true", dest="json_", help="machine-readable JSON")
    p.add_argument(
        "--with-change-id",
        action="store_true",
        default=True,
        help="include Change-Id column (default: on)",
    )
    p.add_argument(
        "--no-change-id",
        action="store_true",
        help="omit Change-Id column",
    )
    p.add_argument("--with-ready-state", action="store_true", help="show ready/blocked state")
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="log git commands and stack resolution to stderr",
    )
    args = p.parse_args(argv)
    configure_logging(args.verbose)
    cwd = cwd_from_env()
    show_cid = args.with_change_id and not args.no_change_id
    logger.debug(
        "gstack cwd=%s json=%s with_change_id=%s with_ready_state=%s",
        cwd,
        args.json_,
        show_cid,
        args.with_ready_state,
    )

    try:
        mb, target, _base_label, commits = build_stack(
            cwd,
            with_ready_state=args.with_ready_state,
        )
    except GitError as e:
        return handle_git_error(e)

    logger.debug(
        "gstack merge_base=%s target=%s commits=%d",
        mb[:8],
        target,
        len(commits),
    )

    if args.json_:
        payload = {
            "merge_base": mb,
            "target_review_branch": target,
            "commits": [
                {
                    "index": c.index,
                    "sha": c.sha,
                    "short_sha": c.short_sha,
                    "subject": c.subject,
                    "change_id": c.change_id,
                    "ready_state": c.ready_state,
                }
                for c in commits
            ],
        }
        print(json.dumps(payload, indent=2))
        return 0

    print(f"Base branch: {target}")
    print(f"Merge base: {mb[:8]}")
    print(f"Target review branch: {target}")
    print()

    if not commits:
        print("(empty stack)")
        return 0

    for c in commits:
        sym = _symbol(c.ready_state) if args.with_ready_state else " "
        cid_part = ""
        if show_cid:
            cid_txt = c.change_id or "(none)"
            cid_part = f"  Change-Id: {cid_txt}"
        ready_part = f"  [{c.ready_state}]" if args.with_ready_state else ""
        subj = c.subject[:48].ljust(48)
        print(f"{c.index:3} {sym} {c.short_sha} {subj}{cid_part}{ready_part}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
