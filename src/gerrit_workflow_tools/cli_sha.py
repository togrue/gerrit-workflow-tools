"""
``ger sha`` — resolve a commit SHA from a Gerrit Change-Id in the local stack

USAGE
    ger sha [--range REV_RANGE] [--all] [--short | --subject] [--json | --ai] CHANGE_ID

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

    --ai, --format ai
        The same line as without it (with --short / --subject too), colour off: text for a model to read.

DEFAULT RANGE
    If --range is omitted, ger sha searches the local stack: upstream..HEAD of the
    working branch, first parents only (core.stack.get_stack_snapshot, as ger log).

EXIT STATUS
    The codes every ger command shares (cli_common.ExitCode, docu/spec/exit-codes.md):
    0 one match, 2 (USAGE) an invalid Change-Id, 3 (NOT_FOUND) no matching commit,
    8 (DUPLICATE_CHANGE_ID) several matching commits (all printed under --all).
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from gerrit_workflow_tools.cli_args import add_color_args, add_output_format_args, add_verbose_and_debug_log_args
from gerrit_workflow_tools.cli_attention import add_attention_query_args, resolve_attention_query_or_exit
from gerrit_workflow_tools.cli_common import ExitCode, init_cli_runtime, run_cli_command
from gerrit_workflow_tools.core.change_id import CHANGE_ID_VALUE_RE, DuplicateChangeIdError
from gerrit_workflow_tools.core.changeish import no_commit_with_change_id_message
from gerrit_workflow_tools.core.gerrit.stack_context import ChangeResolutionError
from gerrit_workflow_tools.core.git_state import current_branch
from gerrit_workflow_tools.core.run_context import RunContext
from gerrit_workflow_tools.core.stack import Commit, commits_in_range, get_stack_snapshot, read_commit_rows
from gerrit_workflow_tools.core.usage import UsageError
from gerrit_workflow_tools.render.style import color_short_sha
from gerrit_workflow_tools.upstream_interactive import require_branch_upstream


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

    add_output_format_args(ap)
    add_color_args(ap)
    add_verbose_and_debug_log_args(ap)
    return ap


def _check_args(ap: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.json_ and (args.short or args.subject):
        ap.error("--short and --subject pick a text line; they do not combine with --json")
    if (args.first_attention or args.next_attention) and (args.rev_range or args.all_commits):
        ap.error("cannot combine attention targeting with --range / --all")
    if args.first_attention and args.change_id:
        ap.error("cannot use CHANGE_ID with --first-attention")
    if not args.first_attention and not args.next_attention and not args.change_id:
        ap.error("CHANGE_ID is required (or use --first-attention / --next-attention)")


def _commits_carrying(run: RunContext, args: argparse.Namespace) -> list[Commit] | int:
    """The commits of the searched range whose footer is ``args.change_id``; ``REFUSED`` on an upstream declined."""
    cwd, settings = run.cwd, run.settings
    if not CHANGE_ID_VALUE_RE.match(args.change_id):
        raise UsageError(
            f"invalid Change-Id: {args.change_id!r}; ger sha takes a full Change-Id (I + 40 hex). "
            "For a commit, change number or other changeish: ger resolve"
        )
    if args.all_commits:
        logger.debug("searching all commits")
        commits = _commits_all(cwd)
    elif args.rev_range:
        logger.debug("searching range: %s", args.rev_range)
        commits = commits_in_range(cwd, args.rev_range)
    else:
        branch = current_branch(cwd)
        if branch != "HEAD" and not require_branch_upstream(cwd, branch, settings=settings):
            return int(ExitCode.REFUSED)
        commits = list(get_stack_snapshot(cwd, settings=settings).commits)
    want = args.change_id.lower()
    matches = [c for c in commits if c.change_id and c.change_id.lower() == want]
    if not matches:
        raise ChangeResolutionError(no_commit_with_change_id_message(args.change_id))
    if len(matches) > 1 and not args.all_commits:
        shorts = ", ".join(color_short_sha(c.short_sha) for c in matches)
        raise DuplicateChangeIdError(f"multiple commits with Change-Id {args.change_id}: {shorts}", [])
    return matches


def main(argv: list[str] | None = None) -> int:
    """CLI entry for ``ger sha``: resolve a Change-Id to a commit SHA in the chosen revision range."""
    return run_cli_command(lambda: _run(argv))


def _run(argv: list[str] | None) -> int:
    ap = _build_parser()
    args = ap.parse_args(argv)
    run = init_cli_runtime(args)
    cwd, settings = run.cwd, run.settings
    _check_args(ap, args)

    matches: list[tuple[str, str, str]] = []
    printed_change_id = args.change_id or ""

    if args.first_attention or args.next_attention:
        branch = run.repo.working_branch
        if branch is not None and not require_branch_upstream(cwd, branch, settings=settings):
            return int(ExitCode.REFUSED)
        resolved = resolve_attention_query_or_exit(
            run,
            args,
            branch=branch,
            after_changeish=args.change_id if args.next_attention else None,
        )
        if isinstance(resolved, int):
            return resolved
        printed_change_id = resolved.change_id or ""
        matches = [(resolved.sha, resolved.short_sha, resolved.summary)]
    else:
        assert args.change_id is not None
        found = _commits_carrying(run, args)
        if isinstance(found, int):
            return found
        matches = [(c.sha, c.short_sha, c.subject) for c in found]

    for sha, short_sha, subject in matches:
        if args.json_:
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
