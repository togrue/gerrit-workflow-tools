"""CLI for inspecting and validating commit Change-Ids."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable
from pathlib import Path

from gerrit_workflow_tools.cli_args import add_color_args, add_verbose_and_debug_log_args
from gerrit_workflow_tools.cli_common import ExitCode, init_cli_runtime, run_cli_command
from gerrit_workflow_tools.core.change_id import ChangeIdRow, change_id_issues_error, classify_issues
from gerrit_workflow_tools.core.changeish import is_change_id
from gerrit_workflow_tools.core.git_run import GitError
from gerrit_workflow_tools.core.git_state import current_branch
from gerrit_workflow_tools.core.reword import add_missing_change_ids
from gerrit_workflow_tools.core.stack import Commit, read_commit_rows, rev_spec_stack_base_to_end
from gerrit_workflow_tools.core.usage import UsageError
from gerrit_workflow_tools.core.words import counted
from gerrit_workflow_tools.render.style import color_short_sha
from gerrit_workflow_tools.upstream_interactive import require_branch_upstream


def _audit_change_ids(commits: Iterable[Commit], *, print_valid: bool = False) -> None:
    """Raise the shared Change-Id error naming every missing, malformed or duplicate Change-Id.

    One ``error:`` line per issue in *commits* order; the exit code is the first issue's. With
    *print_valid*, each commit that has none is printed first, in order.
    """
    commits = list(commits)
    issues = classify_issues(ChangeIdRow(c.sha, c.short_sha, c.change_id) for c in commits)
    flagged = {i.sha for i in issues}
    if print_valid:
        for commit in commits:
            if commit.sha not in flagged:
                print(commit.change_id)
    if issues:
        # run_cli_command prefixes the first line with "error: "; the others get theirs here.
        lines = [f"commit {color_short_sha(i.short_sha)}: {i.detail}" for i in issues]
        raise change_id_issues_error(issues, "\nerror: ".join(lines))


def check_stack_change_ids(cwd: Path) -> int:
    """Raise the shared Change-Id error when the current stack has missing/invalid/duplicate Change-Ids.

    Always scans ``upstream_tip..HEAD`` (the full local stack), oldest first, and names every issue.
    Returns how many commits it checked.
    """
    commits = read_commit_rows(cwd, "--reverse", rev_spec_stack_base_to_end(cwd, "HEAD"))
    _audit_change_ids(commits)
    return len(commits)


def _rev_arg(arg: str) -> str:
    """*arg* as a ``git log`` revision or range, left for git itself to interpret."""
    rev = arg.strip()
    if not rev:
        raise GitError("git log failed: empty revision", stderr="", returncode=-1)
    return rev


def print_change_ids_for_range(cwd: Path, input_arg: str, use_remote: bool) -> None:
    """Print Change-Ids for one commit or a revision range resolved from user input."""
    rev = _rev_arg(input_arg)
    if use_remote:
        rows = read_commit_rows(cwd, "--reverse", rev_spec_stack_base_to_end(cwd, rev))
    elif ".." in rev:
        rows = read_commit_rows(cwd, "--reverse", rev)
    else:
        rows = read_commit_rows(cwd, "-1", rev)
    if not rows:
        print("(no commits in range)", file=sys.stderr)
    # Every commit is reported, as --check reports it: the good ones printed, then each problem.
    _audit_change_ids(rows, print_valid=True)


def main(argv: list[str] | None = None) -> int:
    """CLI for `ger change-id`.

    Prints or validates Change-Ids for commits or ranges, with optional stack checking.
    """
    return run_cli_command(lambda: _run(argv))


def _run(argv: list[str] | None) -> int:
    args = _build_parser().parse_args(argv)
    run = init_cli_runtime(args)
    cwd, settings = run.cwd, run.settings
    input_arg = args.rev_or_range or "HEAD"

    if args.start_at_remote or args.check or args.fix:
        branch = current_branch(cwd)
        if branch != "HEAD" and not require_branch_upstream(cwd, branch, settings=settings):
            return int(ExitCode.REFUSED)
    if args.fix and args.check:
        raise UsageError("--fix cannot be combined with --check")
    if args.fix:
        if is_change_id(input_arg):
            raise UsageError("--fix needs a commit or range, not a Change-Id")
        fixed = add_missing_change_ids(cwd, _rev_arg(input_arg))
        for idx, commit in enumerate(fixed, start=1):
            print(f'{idx}/{len(fixed)} Fixed "{commit.subject}"', file=sys.stderr, flush=True)
    elif args.check:
        if args.rev_or_range is not None:
            raise UsageError("--check always scans the full current stack; do not pass REV_OR_RANGE")
        checked = check_stack_change_ids(cwd)
        if sys.stdout.isatty():
            print(f"{counted(checked, 'commit')}, all Change-Ids valid")
    elif is_change_id(input_arg):
        print(input_arg)
    else:
        print_change_ids_for_range(cwd, input_arg, args.start_at_remote)
    return int(ExitCode.OK)


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the command-line parser for ``ger change-id``."""
    p = argparse.ArgumentParser(
        prog="ger change-id",
        description=(
            "Print the Change-Id of a commit or of each commit in a range; --check validates the whole local "
            "stack (missing, invalid or duplicate Change-Ids), --fix adds the missing ones."
        ),
    )
    p.add_argument(
        "rev_or_range",
        nargs="?",
        default=None,
        metavar="REV_OR_RANGE",
        help=(
            "Revision (anything git rev-parse --verify accepts), Change-Id (I…, passthrough), "
            "or range (rev1..rev2 or rev1...rev2). Defaults to HEAD. Not allowed with --check."
        ),
    )
    add_verbose_and_debug_log_args(p)
    p.add_argument(
        "--start-at-remote",
        action="store_true",
        help=(
            "Use upstream_tip..END (same stack window as default `ger log`) instead of the default revision resolution."
        ),
    )
    p.add_argument(
        "--check",
        action="store_true",
        help=(
            "Validate all commits in the current local stack (upstream_tip..HEAD): "
            "every commit needs a valid unique Change-Id."
        ),
    )
    p.add_argument(
        "--fix",
        action="store_true",
        help=(
            "Assign Change-Ids for commits in upstream_tip..END when the last non-empty message line has no Change-Id."
        ),
    )
    add_color_args(p)
    return p


if __name__ == "__main__":
    raise SystemExit(main())
