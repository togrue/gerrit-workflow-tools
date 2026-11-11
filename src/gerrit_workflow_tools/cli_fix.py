"""CLI for ``ger fix``: create a ``git commit --fixup`` targeting a ref or Gerrit change."""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import sys
from pathlib import Path

from gerrit_workflow_tools.cli_common import (
    add_json_arg,
    add_verbose_and_debug_log_args,
    ask_choice,
    changeish_help,
    init_cli_runtime,
    print_stack_commit_notes,
    run_cli_command,
)
from gerrit_workflow_tools.core.gerrit.change_resolution import resolve_stack_changeish
from gerrit_workflow_tools.core.gerrit.rest import GerritRest
from gerrit_workflow_tools.core.git_run import git, git_out
from gerrit_workflow_tools.core.rebase_state import conflict_next_step, rebase_in_progress
from gerrit_workflow_tools.core.stack import UNSQUASHED_FIXUP_ADVICE, fixup_target_sha, get_stack_snapshot
from gerrit_workflow_tools.core.upstream_interactive import stdin_is_terminal
from gerrit_workflow_tools.core.usage import UsageError


logger = logging.getLogger(__name__)


def _index_has_staged_changes(cwd: Path) -> bool:
    d = git("diff", "--cached", "--quiet", cwd=cwd, check=False)
    return d.returncode != 0


def _worktree_has_unstaged_tracked_changes(cwd: Path) -> bool:
    """True when tracked files have unstaged modifications (``git diff``)."""
    d = git("diff", "--quiet", cwd=cwd, check=False)
    return d.returncode != 0


def _stage_tracked_modifications(cwd: Path) -> None:
    """Stage all modifications/deletions to tracked files (``git add -u``)."""
    git("add", "-u", cwd=cwd)


def _print_unstaged_diff(cwd: Path) -> None:
    """Print the unstaged tracked-file diff to stderr."""
    cp = git("diff", cwd=cwd, check=False)
    text = (cp.stdout or "").rstrip("\n")
    if text:
        print(text, file=sys.stderr)
    else:
        print("(no unstaged diff)", file=sys.stderr)


def _prompt_stage_modified_changes(cwd: Path) -> bool:
    """Interactively offer to stage unstaged tracked modifications.

    Returns True if the user accepted and files were staged. Only runs when
    stdin is a TTY and there are unstaged tracked changes.
    """
    if not stdin_is_terminal():
        return False
    if not _worktree_has_unstaged_tracked_changes(cwd):
        return False

    print(
        "No staged changes. Stage all modifications to tracked files for this fixup?",
        file=sys.stderr,
    )
    answers = {"y": "stage", "yes": "stage", "n": "skip", "no": "skip", "": "skip", "d": "diff", "diff": "diff"}
    while True:
        try:
            with contextlib.redirect_stdout(sys.stderr):
                choice = ask_choice("[y/n/d] (d = show diff): ", answers, hint="Please answer y, n, or d.")
        except (EOFError, KeyboardInterrupt):
            print("", file=sys.stderr)
            return False
        if choice != "diff":
            break
        _print_unstaged_diff(cwd)
    if choice == "stage":
        _stage_tracked_modifications(cwd)
    return choice == "stage"


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the command-line parser for ``ger fix``."""
    p = argparse.ArgumentParser(
        prog="ger fix",
        description=(
            "Create a fixup commit (``git commit --fixup``) for a commit in the current stack. "
            "By default only **staged** changes are committed; use ``-a`` to include all modifications to "
            "tracked files. When the index is empty and stdin is a TTY, you are prompted to stage "
            "tracked modifications (with an option to show the diff)."
        ),
    )
    p.add_argument(
        "target",
        metavar="REF_OR_CHANGE",
        help=changeish_help("The commit to fix, in the current stack"),
    )
    p.add_argument(
        "-a",
        "--all",
        action="store_true",
        dest="commit_all",
        help="Pass ``-a`` to ``git commit`` (stage all modifications to tracked files, then commit).",
    )
    p.add_argument(
        "--no-verify",
        action="store_true",
        help="Bypass pre-commit and commit-msg hooks (passed through to ``git commit``).",
    )
    add_json_arg(p)
    add_verbose_and_debug_log_args(p, debug_log_help="Log resolution steps to stderr.")
    return p


def main(argv: list[str] | None = None, *, gerrit: GerritRest | None = None) -> int:
    """Create a fixup commit with ``git commit --fixup=<target>``."""
    return run_cli_command(lambda: _run(argv, gerrit=gerrit))


def _run(argv: list[str] | None, *, gerrit: GerritRest | None) -> int:
    p = _build_parser()
    args = p.parse_args(argv)
    cwd, settings, _summary_highlighter = init_cli_runtime(args)

    if step := conflict_next_step(cwd):
        raise UsageError(f"the index has unresolved conflicts, and a fixup would commit them; {step}")
    # Same resolution as `ger edit` / `ger reword`: a fixup must name a commit a later
    # `git rebase --autosquash` can actually place (ADR-0003).
    target = resolve_stack_changeish(cwd, args.target, settings=settings, gerrit=gerrit, require_in_stack=True)
    snapshot = get_stack_snapshot(cwd, settings=settings)
    fixup_sha = fixup_target_sha(snapshot.commits, target.sha)
    logger.info("fixup target commit: %s", fixup_sha)

    print_stack_commit_notes(target)

    if (
        not args.commit_all
        and not _index_has_staged_changes(cwd)
        and (not _prompt_stage_modified_changes(cwd) or not _index_has_staged_changes(cwd))
    ):
        raise UsageError(
            "no staged changes (index empty). Stage edits with `git add`, "
            "or use `ger fix -a …` to commit all changes to tracked files."
        )

    cmd: list[str] = ["-c", "core.editor=true", "commit"]
    if args.no_verify:
        cmd.append("--no-verify")
    if args.commit_all:
        cmd.append("-a")
    cmd.extend(["--fixup", fixup_sha])

    cp = git(*cmd, cwd=cwd, check=False)
    if cp.returncode != 0:
        print(cp.stderr.strip() or cp.stdout.strip() or "git commit failed", file=sys.stderr)
        return cp.returncode or 1

    if args.json_:
        payload: dict[str, object] = {"fixup_sha": fixup_sha}
        if target.resolution is not None:
            payload["resolution"] = target.resolution.to_json_dict()
        print(json.dumps(payload, indent=2))
    else:
        print(git_out("log", "-1", "--format=%h %s", cwd=cwd))
        # The same advice `ger push` and `ger log` give for this commit.
        first = "finish the rebase (`git rebase --continue`), then " if rebase_in_progress(cwd) else ""
        print(f"next: {first}{UNSQUASHED_FIXUP_ADVICE}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
