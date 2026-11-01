"""
``ger sha`` — resolve a commit SHA from a Gerrit Change-Id in the current commit chain

USAGE
    ger sha [--range REV_RANGE] [--all] [--short | --subject | --json] CHANGE_ID

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
    If --range is omitted, ger sha searches the current Gerrit stack using:
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

from gerrit_workflow_tools.cli_attention import add_attention_query_args, resolve_attention_query_or_exit
from gerrit_workflow_tools.cli_common import (
    HELP_JSON,
    ExitCode,
    add_color_args,
    add_verbose_and_debug_log_args,
    init_cli_runtime,
    run_cli_command,
)
from gerrit_workflow_tools.cli_style import color_short_sha
from gerrit_workflow_tools.core.change_id import CHANGE_ID_VALUE_RE
from gerrit_workflow_tools.core.git_state import current_branch, resolve_working_branch
from gerrit_workflow_tools.core.stack import Commit, commits_in_range, merge_base_with_target, read_commit_rows
from gerrit_workflow_tools.core.upstream_interactive import require_branch_upstream


logger = logging.getLogger(__name__)


def _commits_all(cwd: Path) -> list[Commit]:
    return read_commit_rows(cwd, "--all", "--reverse")


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the command-line parser for ``ger sha``."""
    ap = argparse.ArgumentParser(
        prog="ger sha",
        description="Resolve a Gerrit Change-Id to a Git commit SHA.",
    )
    ap.add_argument(
        "change_id",
        nargs="?",
        metavar="CHANGE_ID",
        help="Gerrit Change-Id to look up (I + 40 hex digits). Optional with --first-attention / --next-attention.",
    )
    add_attention_query_args(ap)

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
    return ap


def main(argv: list[str] | None = None) -> int:
    """CLI entry for ``ger sha``: resolve a Change-Id to a commit SHA in the chosen revision range."""
    return run_cli_command(lambda: _run(argv))


def _run(argv: list[str] | None) -> int:  # pylint: disable=too-many-branches,too-many-locals
    ap = _build_parser()
    args = ap.parse_args(argv)
    cwd, settings, _summary_highlighter = init_cli_runtime(args)

    if (args.first_attention or args.next_attention) and (args.rev_range or args.all_commits):
        ap.error("cannot combine attention targeting with --range / --all")
    if args.first_attention and args.change_id:
        ap.error("cannot use CHANGE_ID with --first-attention")
    if not args.first_attention and not args.next_attention and not args.change_id:
        ap.error("CHANGE_ID is required (or use --first-attention / --next-attention)")

    matches: list[tuple[str, str, str]] = []
    printed_change_id = args.change_id or ""

    if args.first_attention or args.next_attention:
        branch = resolve_working_branch(cwd, settings=settings)
        if branch is not None and not require_branch_upstream(cwd, branch, settings=settings):
            return int(ExitCode.ATTENTION)
        resolved = resolve_attention_query_or_exit(
            cwd,
            args,
            settings=settings,
            branch=branch,
            after_changeish=args.change_id if args.next_attention else None,
        )
        if isinstance(resolved, int):
            return resolved
        printed_change_id = resolved.change_id or ""
        matches = [(resolved.sha, resolved.short_sha, resolved.summary)]
    else:
        assert args.change_id is not None
        if not CHANGE_ID_VALUE_RE.match(args.change_id):
            print(f"error: invalid Change-Id: {args.change_id!r}", file=sys.stderr)
            return int(ExitCode.USAGE)

        want = args.change_id.lower()

        if args.all_commits:
            logger.debug("searching all commits")
            commits = _commits_all(cwd)
        elif args.rev_range:
            logger.debug("searching range: %s", args.rev_range)
            commits = commits_in_range(cwd, args.rev_range)
        else:
            branch = current_branch(cwd)
            if branch != "HEAD" and not require_branch_upstream(cwd, branch, settings=settings):
                return 1
            _fork, display, target_tip = merge_base_with_target(cwd)
            rev_range = f"{target_tip}..HEAD"
            logger.debug("default range: %s (base: %s)", rev_range[:20], display)
            commits = commits_in_range(cwd, rev_range)

        for c in commits:
            cid = c.change_id
            if cid and cid.lower() == want:
                matches.append((c.sha, c.short_sha, c.subject))

        if not matches:
            print(f"error: no commit found with Change-Id {args.change_id}", file=sys.stderr)
            return int(ExitCode.NOT_FOUND)

        if len(matches) > 1 and not args.all_commits:
            shorts = ", ".join(color_short_sha(m[1]) for m in matches)
            print(
                f"error: multiple commits with Change-Id {args.change_id}: {shorts}",
                file=sys.stderr,
            )
            return int(ExitCode.DUPLICATE_CHANGE_ID)

    for sha, short_sha, subject in matches:
        if args.json_out:
            print(json.dumps({"change_id": printed_change_id, "sha": sha, "subject": subject}))
        elif args.subject:
            print(f"{color_short_sha(short_sha)} {subject}")
        elif args.short:
            print(color_short_sha(short_sha))
        else:
            print(sha)

    return int(ExitCode.DUPLICATE_CHANGE_ID) if len(matches) > 1 else int(ExitCode.OK)


if __name__ == "__main__":
    raise SystemExit(main())
