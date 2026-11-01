"""CLI for inspecting and validating commit Change-Ids."""

import argparse
import sys
from collections.abc import Iterable
from pathlib import Path

from gerrit_workflow_tools.cli_common import (
    ExitCode,
    add_color_args,
    add_verbose_and_debug_log_args,
    init_cli_runtime,
    run_cli_command,
)
from gerrit_workflow_tools.cli_style import color_short_sha
from gerrit_workflow_tools.core.change_id import (
    ChangeIdIssueKind,
    ChangeIdRow,
    append_change_id_footer,
    classify_issues,
    extract_valid_change_id,
    generate_change_id_from_idents,
    strip_change_id_lines,
)
from gerrit_workflow_tools.core.changeish import is_change_id
from gerrit_workflow_tools.core.git_run import GitError, commit_sha_or_none, git
from gerrit_workflow_tools.core.git_state import current_branch, rebase_in_progress
from gerrit_workflow_tools.core.stack import (
    Commit,
    read_commit_rows,
    read_rewrite_rows,
    rev_spec_stack_base_to_end,
)
from gerrit_workflow_tools.core.upstream_interactive import require_branch_upstream


class ChangeIdError(Exception):
    """Custom exception for Change-Id processing errors."""

    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code


def _audit_change_ids(commits: Iterable[Commit]) -> None:
    """Raise :class:`ChangeIdError` for the first missing, malformed or duplicate Change-Id."""
    issues, _exit = classify_issues(ChangeIdRow(c.sha, c.short_sha, c.change_id) for c in commits)
    if issues:
        issue = issues[0]
        duplicate = issue.kind == ChangeIdIssueKind.DUPLICATE
        raise ChangeIdError(
            f"error: commit {color_short_sha(issue.short_sha)}: {issue.detail}",
            code=int(ExitCode.DUPLICATE_CHANGE_ID if duplicate else ExitCode.MISSING_CHANGE_ID),
        )


def check_stack_change_ids(cwd) -> None:
    """Raise :class:`ChangeIdError` when the current stack has missing/invalid/duplicate Change-Ids.

    Always scans ``upstream_tip..HEAD`` (the full local stack).
    """
    _audit_change_ids(read_commit_rows(cwd, rev_spec_stack_base_to_end(cwd, "HEAD")))


def _rev_arg(arg: str) -> str:
    """*arg* as a ``git log`` revision or range, left for git itself to interpret."""
    rev = arg.strip()
    if not rev:
        raise GitError("git log failed: empty revision", stderr="", returncode=-1)
    return rev


def print_change_ids_for_range(cwd, input_arg, use_remote: bool) -> None:
    """Print Change-Ids for one commit or a revision range resolved from user input."""
    rev = _rev_arg(input_arg)
    if use_remote:
        rows = read_commit_rows(cwd, "--reverse", rev_spec_stack_base_to_end(cwd, rev))
    elif ".." in rev:
        rows = read_commit_rows(cwd, "--reverse", rev)
    else:
        rows = read_commit_rows(cwd, "-1", rev)
    for row in rows:
        _audit_change_ids([row])
        print(row.change_id)


def _ensure_safe_for_fix(cwd: Path) -> None:
    """Block --fix during merge/rebase; uncommitted changes are fine (message-only rewrite)."""
    if commit_sha_or_none(cwd, "MERGE_HEAD"):
        raise ChangeIdError("error: --fix cannot run during an in-progress merge", code=int(ExitCode.USAGE))
    if rebase_in_progress(cwd):
        raise ChangeIdError("error: --fix cannot run during an in-progress rebase", code=int(ExitCode.USAGE))


def _commit_tree(cwd: Path, tree: str, parents: list[str], message: str, ident_env: dict[str, str]) -> str:
    """Create a commit with *message* (via stdin) and return its SHA."""
    args = ["commit-tree", tree, "-F", "-"]
    for parent in parents:
        args.extend(["-p", parent])
    p = git(*args, cwd=cwd, env=ident_env, input=message, check=False)
    if p.returncode != 0:
        raise GitError(
            "git commit-tree failed",
            stderr=p.stderr.strip() or p.stdout.strip(),
            returncode=p.returncode,
        )
    return p.stdout.strip()


def fix_change_ids_for_stack(cwd: Path, input_arg: str) -> None:
    """Rewrite stack commit messages, assigning Change-Ids where missing on the last line."""
    if is_change_id(input_arg):
        raise ChangeIdError("error: --fix needs a commit or range, not a Change-Id", code=int(ExitCode.USAGE))

    _ensure_safe_for_fix(cwd)
    rows = read_rewrite_rows(cwd, rev_spec_stack_base_to_end(cwd, _rev_arg(input_arg)))
    needs_fix = [c for c in rows if not extract_valid_change_id(c.body)]
    if not needs_fix:
        return

    # Commits before the first message change keep their SHAs; only rewrite from there.
    first_fix_sha = needs_fix[0].sha
    start = next(i for i, c in enumerate(rows) if c.sha == first_fix_sha)
    to_rewrite = rows[start:]

    total = len(needs_fix)
    fix_progress = {c.sha: (i, c.subject) for i, c in enumerate(needs_fix, start=1)}

    head_ref = current_branch(cwd)
    sha_map: dict[str, str] = {}
    new_head: str | None = None
    for commit in to_rewrite:
        new_parents = [sha_map.get(p, p) for p in commit.parents]

        if commit.sha in fix_progress:
            idx, subject = fix_progress[commit.sha]
            print(f'{idx}/{total} Fixed "{subject}"', file=sys.stderr, flush=True)
            base_msg = strip_change_id_lines(commit.body)
            first_parent = commit.parents[0] if commit.parents else None
            cid = generate_change_id_from_idents(commit.committer_ident, first_parent, base_msg)
            message = append_change_id_footer(base_msg, cid)
        else:
            message = commit.body

        new_sha = _commit_tree(cwd, commit.tree, new_parents, message, commit.ident_env)
        sha_map[commit.sha] = new_sha
        new_head = new_sha

    assert new_head is not None
    ref = "HEAD" if head_ref == "HEAD" else f"refs/heads/{head_ref}"
    update = git("update-ref", ref, new_head, cwd=cwd, check=False)
    if update.returncode != 0:
        raise ChangeIdError(
            f"error: --fix failed: {update.stderr.strip() or update.stdout.strip()}",
            code=int(ExitCode.GIT),
        )


def main(argv: list[str] | None = None) -> int:
    """CLI for `ger change-id`.

    Prints or validates Change-Ids for commits or ranges, with optional stack checking.
    """
    return run_cli_command(lambda: _run(argv))


def _run(argv: list[str] | None) -> int:
    args = _build_parser().parse_args(argv)
    cwd, settings, _summary_highlighter = init_cli_runtime(args)

    input_arg = args.rev_or_range or "HEAD"

    try:
        needs_stack_upstream = bool(args.start_at_remote or args.check or args.fix)
        if needs_stack_upstream:
            branch = current_branch(cwd)
            if branch != "HEAD" and not require_branch_upstream(cwd, branch, settings=settings):
                return 1

        if args.fix and args.check:
            raise ChangeIdError("error: --fix cannot be combined with --check", code=int(ExitCode.USAGE))

        if args.fix:
            fix_change_ids_for_stack(cwd, input_arg)
            return 0

        if args.check:
            if args.rev_or_range is not None:
                raise ChangeIdError(
                    "error: --check always scans the full current stack; do not pass REV_OR_RANGE",
                    code=int(ExitCode.USAGE),
                )
            check_stack_change_ids(cwd)
            return 0

        if is_change_id(input_arg):
            print(input_arg)
            return 0

        print_change_ids_for_range(cwd, input_arg, args.start_at_remote)
        return 0

    except ChangeIdError as err:
        print(str(err), file=sys.stderr)
        return err.code


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the command-line parser for ``ger change-id``."""
    p = argparse.ArgumentParser(prog="ger change-id")
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
