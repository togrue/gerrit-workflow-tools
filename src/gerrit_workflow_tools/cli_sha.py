"""
gsha — resolve a commit SHA from a Gerrit Change-Id in the current commit chain

USAGE
    gsha [--range REV_RANGE] [--all] [--short | --subject | --json] CHANGE_ID

DESCRIPTION
    Searches commits in the selected revision range for a commit message footer
    of the form:

        Change-Id: I...

    If exactly one matching commit is found, prints its full commit SHA.

OPTIONS
    --range REV_RANGE
        Git revision range to search, e.g.
            origin/main..HEAD
            main@{upstream}..HEAD
            HEAD@{gerrit-branch}..HEAD

    --all
        Search all commits reachable from any ref in the repository.

    --short
        Print the abbreviated commit SHA instead of the full SHA.

    --subject
        Print the abbreviated SHA followed by the commit subject line.

    --json
        Print a JSON object with keys: change_id, sha, subject.

DEFAULT RANGE
    If --range is omitted, gsha searches the current Gerrit stack using:
      1. configured Gerrit base range, if available
      2. branch upstream..HEAD, if available
      3. configured default target branch merge-base..HEAD

EXIT STATUS
    0  one match found
    1  usage error / invalid Change-Id
    2  no matching commit found
    3  multiple matching commits found
    4  git/repository error
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from gerrit_workflow_tools.change_id import CHANGE_ID_VALUE_RE
from gerrit_workflow_tools.cli_common import (
    HELP_JSON,
    add_color_args,
    add_verbose_and_debug_log_args,
    configure_logging,
    cwd_from_env,
)
from gerrit_workflow_tools.cli_style import color_short_sha, init_color_mode
from gerrit_workflow_tools.git_run import GitError, git
from gerrit_workflow_tools.stack import _parse_rs_metadata_records, commits_in_range, merge_base_with_target

logger = logging.getLogger(__name__)

_LOG_FMT = "%H%x1e%h%x1e%s%x1e%B%x1e"


def _commits_all(cwd: Path):
    p = git("log", "--all", "--reverse", f"--format={_LOG_FMT}", cwd=cwd, check=False)
    if p.returncode != 0:
        raise GitError("git log --all failed", stderr=p.stderr, returncode=p.returncode)
    return _parse_rs_metadata_records(p.stdout)


def main(argv: list[str] | None = None) -> int:
    """CLI entry for ``ger sha``: resolve a Change-Id to a commit SHA in the chosen revision range."""
    ap = argparse.ArgumentParser(
        prog="ger sha",
        description="Resolve a Gerrit Change-Id to a Git commit SHA.",
    )
    ap.add_argument(
        "change_id",
        metavar="CHANGE_ID",
        help="Gerrit Change-Id to look up (I + 40 hex digits).",
    )

    range_group = ap.add_mutually_exclusive_group()
    range_group.add_argument(
        "--range",
        metavar="REV_RANGE",
        dest="rev_range",
        help="Git revision range to search.",
    )
    range_group.add_argument(
        "--all",
        action="store_true",
        dest="all_commits",
        help="Search all commits reachable from any ref.",
    )

    out_group = ap.add_mutually_exclusive_group()
    out_group.add_argument("--short", action="store_true", help="Print abbreviated SHA.")
    out_group.add_argument(
        "--subject",
        action="store_true",
        help="Print abbreviated SHA and commit subject.",
    )
    out_group.add_argument("--json", action="store_true", dest="json_out", help=HELP_JSON)

    add_color_args(ap)
    add_verbose_and_debug_log_args(ap)

    args = ap.parse_args(argv)
    configure_logging(args.debug_log)
    init_color_mode(color=args.color)
    cwd = cwd_from_env()

    if not CHANGE_ID_VALUE_RE.match(args.change_id):
        print(f"error: invalid Change-Id: {args.change_id!r}", file=sys.stderr)
        return 1

    want = args.change_id.lower()

    try:
        if args.all_commits:
            logger.debug("searching all commits")
            commits = _commits_all(cwd)
        elif args.rev_range:
            logger.debug("searching range: %s", args.rev_range)
            commits = commits_in_range(cwd, args.rev_range)
        else:
            _fork, display, target_tip = merge_base_with_target(cwd)
            rev_range = f"{target_tip}..HEAD"
            logger.debug("default range: %s (base: %s)", rev_range[:20], display)
            commits = commits_in_range(cwd, rev_range)
    except GitError as e:
        print(f"error: {e}", file=sys.stderr)
        return 4

    matches: list[tuple[str, str, str]] = []
    for c in commits:
        cid = c.change_id
        if cid and cid.lower() == want:
            matches.append((c.sha, c.short_sha, c.subject))

    if not matches:
        print(f"error: no commit found with Change-Id {args.change_id}", file=sys.stderr)
        return 2

    if len(matches) > 1 and not args.all_commits:
        shorts = ", ".join(color_short_sha(m[1]) for m in matches)
        print(
            f"error: multiple commits with Change-Id {args.change_id}: {shorts}",
            file=sys.stderr,
        )
        return 3

    for sha, short_sha, subject in matches:
        if args.json_out:
            print(json.dumps({"change_id": args.change_id, "sha": sha, "subject": subject}))
        elif args.subject:
            print(f"{color_short_sha(short_sha)} {subject}")
        elif args.short:
            print(color_short_sha(short_sha))
        else:
            print(sha)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
