from __future__ import annotations

import argparse
import json
import sys

from gerrit_workflow_tools.change_id import classify_issues
from gerrit_workflow_tools.cli_common import cwd_from_env, handle_git_error
from gerrit_workflow_tools.git_run import GitError, git_out
from gerrit_workflow_tools.ready_calc import (
    change_id_rows_for_range,
    change_id_rows_for_rev_range,
)
from gerrit_workflow_tools.stack import merge_base_with_target


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="git gchangeid-check")
    p.add_argument("--json", action="store_true", dest="json_", help="JSON output")
    p.add_argument(
        "--range", metavar="RANGE", help="only check commits in range (e.g. base..HEAD)"
    )
    p.add_argument(
        "--strict",
        action="store_true",
        help="treat malformed Change-Id as error (default)",
    )
    p.add_argument(
        "--lenient", action="store_true", help="malformed Change-Id is warning only"
    )
    args = p.parse_args(argv)
    cwd = cwd_from_env()
    strict = args.strict or not args.lenient

    try:
        if args.range:
            raw = args.range
            if ".." not in raw:
                print("error: --range must look like base..HEAD", file=sys.stderr)
                return 2
            idx = raw.index("..")
            left = raw[:idx].strip()
            right = raw[idx + 2 :].strip()
            if not right:
                right = "HEAD"
            if not left:
                print(
                    "error: --range must include a left endpoint (e.g. merge-base..HEAD)",
                    file=sys.stderr,
                )
                return 2
            start = git_out("rev-parse", left, cwd=cwd)
            end = git_out("rev-parse", right, cwd=cwd)
            rows = change_id_rows_for_rev_range(cwd, start, end)
        else:
            mb, _, _ = merge_base_with_target(cwd)
            rows = change_id_rows_for_range(cwd, mb)
    except GitError as e:
        return handle_git_error(e)

    items = [(a, b, c) for a, b, c in rows]
    issues, exit_code = classify_issues(items, strict=strict)

    if args.json_:
        print(
            json.dumps(
                {
                    "exit_code": exit_code,
                    "issues": [
                        {
                            "kind": i.kind,
                            "sha": i.sha,
                            "short_sha": i.short_sha,
                            "detail": i.detail,
                            "severity": i.severity,
                        }
                        for i in issues
                    ],
                },
                indent=2,
            )
        )
        return exit_code

    if not issues:
        print("Change-Id check: OK")
        return 0

    for i in issues:
        print(
            f"{i.severity.upper()}: {i.short_sha} {i.kind}: {i.detail}", file=sys.stderr
        )

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
