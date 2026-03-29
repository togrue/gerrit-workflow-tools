from __future__ import annotations

import argparse
import json

from gerrit_workflow_tools.cli_common import cwd_from_env, handle_git_error
from gerrit_workflow_tools.git_run import GitError
from gerrit_workflow_tools.ready_calc import compute_ready


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="git gready")
    p.add_argument(
        "--ignore-pattern",
        action="append",
        default=[],
        metavar="REGEX",
        help="ignore this configured stop pattern (repeatable)",
    )
    p.add_argument(
        "--no-config-patterns",
        action="store_true",
        help="do not use gerrit.stopPattern values",
    )
    p.add_argument(
        "--all", action="store_true", dest="all_", help="treat entire stack as pushable"
    )
    p.add_argument("--json", action="store_true", dest="json_", help="JSON output")
    p.add_argument(
        "--until",
        metavar="REV",
        help="limit pushable tip to this commit (must be before boundary)",
    )
    args = p.parse_args(argv)
    cwd = cwd_from_env()

    try:
        r = compute_ready(
            cwd,
            all_commits=args.all_,
            no_config_patterns=args.no_config_patterns,
            ignore_patterns=args.ignore_pattern or None,
            until=args.until,
        )
    except GitError as e:
        return handle_git_error(e)

    if args.json_:
        print(
            json.dumps(
                {
                    "push_mode": r.push_mode,
                    "pushable_commits": r.pushable_count,
                    "boundary_commit": r.boundary_sha,
                    "boundary_reason": r.boundary_reason,
                    "merge_base": r.merge_base,
                    "push_tip": r.push_tip_sha,
                    "push_range": r.push_range,
                },
                indent=2,
            )
        )
        return 0

    print(f"Push mode: {r.push_mode}")
    print(f"Pushable commits: {r.pushable_count}")
    print(f"Boundary commit: {r.boundary_sha or '(none)'}")
    print(f"Boundary reason: {r.boundary_reason}")
    print()
    print("Push range:")
    if r.push_range:
        print(f"  {r.push_range}")
    else:
        print("  (none)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
