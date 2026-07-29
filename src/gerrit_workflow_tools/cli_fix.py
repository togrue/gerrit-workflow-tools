"""CLI for ``ger fix``: create a ``git commit --fixup`` targeting a ref or Gerrit change."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from gerrit_workflow_tools.cli_common import (
    HELP_JSON,
    add_verbose_and_debug_log_args,
    configure_logging,
    cwd_from_env,
    run_cli_command,
)
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.change_resolution import format_resolution_note, resolve_stack_changeish
from gerrit_workflow_tools.core.gerrit.rest import GerritRest
from gerrit_workflow_tools.core.git_run import git

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
    if not sys.stdin.isatty():
        return False
    if not _worktree_has_unstaged_tracked_changes(cwd):
        return False

    print(
        "No staged changes. Stage all modifications to tracked files for this fixup?",
        file=sys.stderr,
    )
    while True:
        try:
            print("[y/n/d] (d = show diff): ", end="", file=sys.stderr, flush=True)
            raw = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("", file=sys.stderr)
            return False
        if raw in ("y", "yes"):
            _stage_tracked_modifications(cwd)
            return True
        if raw in ("n", "no", ""):
            return False
        if raw in ("d", "diff"):
            _print_unstaged_diff(cwd)
            continue
        print("Please answer y, n, or d.", file=sys.stderr)


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the command-line parser for ``ger fix``."""
    p = argparse.ArgumentParser(
        prog="ger fix",
        description=(
            "Create a fixup commit (``git commit --fixup``) targeting a local ref, a ``refs/changes/…`` ref, "
            "or a Gerrit change (numeric id or Change-Id). "
            "By default only **staged** changes are committed; use ``-a`` to include all modifications to "
            "tracked files. When the index is empty and stdin is a TTY, you are prompted to stage "
            "tracked modifications (with an option to show the diff)."
        ),
    )
    p.add_argument(
        "target",
        metavar="REF_OR_CHANGE",
        help=(
            "Commit-ish (branch, SHA, HEAD~n, …), a Gerrit ``refs/changes/AA/NNNNN/PS`` ref, "
            "``change:<n>`` for a numeric change id, or a Change-Id (I…)."
        ),
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
    p.add_argument("--json", action="store_true", dest="json_", help=HELP_JSON)
    add_verbose_and_debug_log_args(p, debug_log_help="Log resolution steps to stderr.")
    return p


def main(argv: list[str] | None = None, *, gerrit: GerritRest | None = None) -> int:
    """Create a fixup commit with ``git commit --fixup=<target>``."""
    return run_cli_command(lambda: _run(argv, gerrit=gerrit))


def _run(argv: list[str] | None, *, gerrit: GerritRest | None) -> int:
    p = _build_parser()
    args = p.parse_args(argv)
    configure_logging(args.debug_log)
    cwd = cwd_from_env()
    settings = Settings.from_cwd(cwd)

    # Same resolution as `ger edit` / `ger reword`: a fixup must name a commit a later
    # `git rebase --autosquash` can actually place (ADR-0003).
    target = resolve_stack_changeish(cwd, args.target, settings=settings, gerrit=gerrit, require_in_stack=True)
    fixup_sha = target.sha
    logger.info("fixup target commit: %s", fixup_sha)

    if target.resolution is not None:
        note = format_resolution_note(target.resolution)
        if note:
            print(note, file=sys.stderr)

    if (
        not args.commit_all
        and not _index_has_staged_changes(cwd)
        and (not _prompt_stage_modified_changes(cwd) or not _index_has_staged_changes(cwd))
    ):
        print(
            "error: no staged changes (index empty). Stage edits with `git add`, "
            "or use `ger fix -a …` to commit all changes to tracked files.",
            file=sys.stderr,
        )
        return 1

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
