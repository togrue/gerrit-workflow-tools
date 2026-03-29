from __future__ import annotations

import argparse
import json

from gerrit_workflow_tools.cli_common import cwd_from_env, handle_git_error
from gerrit_workflow_tools.git_run import GitError
from gerrit_workflow_tools.stack import build_stack


def _symbol(state: str) -> str:
    if state == "ready":
        return "✓"
    if state.startswith("blocked"):
        return "!"
    return "x"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="git gstack")
    p.add_argument("--oneline", action="store_true", help="compact one line per commit")
    p.add_argument(
        "--json", action="store_true", dest="json_", help="machine-readable JSON"
    )
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
    p.add_argument(
        "--with-ready-state", action="store_true", help="show ready/blocked state"
    )
    args = p.parse_args(argv)
    cwd = cwd_from_env()
    show_cid = args.with_change_id and not args.no_change_id

    try:
        mb, target, _base_label, commits = build_stack(
            cwd,
            with_ready_state=args.with_ready_state,
        )
    except GitError as e:
        return handle_git_error(e)

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
        if args.oneline:
            extra = cid_part + ready_part
            print(f"{c.index} {sym} {c.short_sha} {c.subject}{extra}")
        else:
            subj = c.subject[:48].ljust(48)
            print(f"{c.index:2} {sym} {c.short_sha} {subj}{cid_part}{ready_part}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
