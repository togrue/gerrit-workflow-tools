from __future__ import annotations

import argparse
import json
import logging
import sys

from gerrit_workflow_tools.change_id import classify_issues
from gerrit_workflow_tools.cli_common import configure_logging, cwd_from_env, handle_git_error
from gerrit_workflow_tools.git_run import GitError, git_out
from gerrit_workflow_tools.ready_calc import (
    change_id_rows_for_range,
    change_id_rows_for_rev_range,
)
from gerrit_workflow_tools.stack import merge_base_with_target

logger = logging.getLogger(__name__)


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
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="log git commands and checked revisions to stderr",
    )
    args = p.parse_args(argv)
    configure_logging(args.verbose)
    cwd = cwd_from_env()
    strict = args.strict or not args.lenient
    logger.debug(
        "gchangeid-check cwd=%s range=%s strict=%s",
        cwd,
        args.range,
        strict,
    )

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
            logger.debug("gchangeid-check rev range %s..%s (%s..%s)", left, right, start[:8], end[:8])
            rows = change_id_rows_for_rev_range(cwd, start, end)
        else:
            mb, _, _ = merge_base_with_target(cwd)
            logger.debug("gchangeid-check stack from merge_base=%s", mb[:8])
            rows = change_id_rows_for_range(cwd, mb)
    except GitError as e:
        return handle_git_error(e)

    logger.debug("gchangeid-check rows=%d", len(rows))
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
