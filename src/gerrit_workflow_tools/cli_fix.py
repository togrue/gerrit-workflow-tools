"""CLI for ``ger fix``: create a ``git commit --fixup`` targeting a ref or Gerrit change."""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import sys
from pathlib import Path

from gerrit_workflow_tools.cli_args import add_output_format_args, add_verbose_and_debug_log_args, changeish_help
from gerrit_workflow_tools.cli_common import ExitCode, init_cli_runtime, may_prompt, run_cli_command
from gerrit_workflow_tools.cli_notes import print_stack_commit_notes
from gerrit_workflow_tools.cli_terminal import ask_choice
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.change_resolution import resolve_stack_changeish
from gerrit_workflow_tools.core.gerrit.rest import GerritRest
from gerrit_workflow_tools.core.git_run import git, git_out
from gerrit_workflow_tools.core.rebase_next_step import conflict_next_step
from gerrit_workflow_tools.core.rebase_state import rebase_underway
from gerrit_workflow_tools.core.reorder import ReorderRefusal, fold_fixup
from gerrit_workflow_tools.core.reorder_preview import FoldConflict, PickStop, PreviewUnavailable, fixup_fold_conflict
from gerrit_workflow_tools.core.review_checkout import staged_review_refusal
from gerrit_workflow_tools.core.rewrite_refusal import closed_change_refusal
from gerrit_workflow_tools.core.stack import (
    UNSQUASHED_FIXUP_ADVICE,
    Commit,
    StackSnapshot,
    fixup_target_sha,
    get_stack_snapshot,
)
from gerrit_workflow_tools.core.usage import UsageError
from gerrit_workflow_tools.core.words import counted, listed
from gerrit_workflow_tools.render.wrap import say


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


def _prompt_stage_modified_changes(cwd: Path, *, may_ask: bool) -> bool:
    """Interactively offer to stage unstaged tracked modifications.

    Returns True if the user accepted and files were staged. Only runs when
    it may ask (human output on a TTY) and there are unstaged tracked changes.
    """
    if not may_ask:
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
            "Create a fixup commit (git commit --fixup) for a commit in the current stack. "
            "By default only staged changes are committed; use -a to include all modifications to "
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
        help="Pass -a to git commit (stage all modifications to tracked files, then commit).",
    )
    p.add_argument(
        "--no-verify",
        action="store_true",
        help="Bypass pre-commit and commit-msg hooks (passed through to git commit).",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Create the fixup even when it would not fold cleanly into its target.",
    )
    p.add_argument(
        "--fold",
        action="store_true",
        help="Fold the fixup into its target right away (what `ger rebase --autosquash` would do for it).",
    )
    add_output_format_args(p)
    add_verbose_and_debug_log_args(p)
    return p


def main(argv: list[str] | None = None, *, gerrit: GerritRest | None = None) -> int:
    """Create a fixup commit with ``git commit --fixup=<target>``."""
    return run_cli_command(lambda: _run(argv, gerrit=gerrit))


def _run(argv: list[str] | None, *, gerrit: GerritRest | None) -> int:
    p = _build_parser()
    args = p.parse_args(argv)
    run = init_cli_runtime(args, gerrit=gerrit)
    cwd, settings = run.cwd, run.settings

    if refusal := staged_review_refusal(run.worktree.repository_dir, cwd):
        raise UsageError(refusal)
    if step := conflict_next_step(cwd):
        raise UsageError(f"the index has unresolved conflicts, and a fixup would commit them; {step}")
    # Same resolution as `ger edit` / `ger reword`: a fixup must name a commit a later
    # `git rebase --autosquash` can actually place (ADR-0003).
    snapshot = get_stack_snapshot(cwd, run.repo.working_branch, settings=settings)
    target = resolve_stack_changeish(cwd, args.target, service=run.gerrit, require_in_stack=True, stack=snapshot)
    fixup_sha = fixup_target_sha(snapshot.commits, target.sha)
    logger.info("fixup target commit: %s", fixup_sha)

    print_stack_commit_notes(target)
    # The commit the fixup will name: past a fixup of a fixup, the original's change is the one to ask about.
    commit = next(c for c in snapshot.commits if c.sha == fixup_sha)
    if refusal := closed_change_refusal(run.gerrit, commit.valid_change_id, commit=commit.short_sha):
        raise UsageError(refusal)

    if (
        not args.commit_all
        and not _index_has_staged_changes(cwd)
        and (not _prompt_stage_modified_changes(cwd, may_ask=may_prompt(args)) or not _index_has_staged_changes(cwd))
    ):
        raise UsageError(
            "no staged changes (index empty). Stage edits with `git add`, "
            "or use `ger fix -a …` to commit all changes to tracked files."
        )

    rebasing = rebase_underway(run.worktree.repository_dir)
    if rebasing and args.fold:
        raise UsageError("a rebase is underway: finish it (`ger rebase --continue`) before folding a fixup with --fold")
    folds = None if rebasing else _folds_cleanly(cwd, snapshot, commit, args)

    cmd: list[str] = ["-c", "core.editor=true", "commit"]
    if args.no_verify:
        cmd.append("--no-verify")
    if args.commit_all:
        cmd.append("-a")
    cmd.extend(["--fixup", fixup_sha])

    cp = git(*cmd, cwd=cwd, check=False)
    if cp.returncode != 0:
        print(cp.stderr.strip() or cp.stdout.strip() or "git commit failed", file=sys.stderr)
        return cp.returncode  # git's own code, as for every child process (exit-codes.md)

    made = git_out("log", "-1", "--format=%h %s", cwd=cwd)
    folded = _fold_now(cwd, commit, made, settings=settings) if args.fold else None
    if args.json_:
        payload: dict[str, object] = {"fixup_sha": fixup_sha, "folds_cleanly": folds, "folded": folded is not None}
        if target.resolution is not None:
            payload["resolution"] = target.resolution.to_json_dict()
        print(json.dumps(payload, indent=2))
    elif folded is not None:
        print(folded)
    else:
        print(made)
        clean = f"it folds cleanly into {commit.short_sha}; " if folds else ""
        # The same advice `ger push` and `ger log` give for this commit.
        first = "finish the rebase (`ger rebase --continue`), then " if rebasing else ""
        say(f"{clean}next: {first}{UNSQUASHED_FIXUP_ADVICE}")
    return int(ExitCode.OK)


def _folds_cleanly(cwd: Path, snapshot: StackSnapshot, target: Commit, args: argparse.Namespace) -> bool | None:
    """Whether the fixup about to be made folds cleanly into *target*; refuses unless ``--force`` when not."""
    try:
        conflict = fixup_fold_conflict(cwd, snapshot, target, all_tracked=args.commit_all)
    except PreviewUnavailable as exc:
        logger.debug("fold check skipped: %s", exc)
        return None
    if conflict is None or args.force:
        return conflict is None
    raise UsageError(_fold_refusal(conflict, args))


def _fold_refusal(conflict: FoldConflict, args: argparse.Namespace) -> str:
    target, stop = conflict.target, conflict.stop
    files = listed(list(stop.files)) or "its files"
    if stop.commit not in conflict.in_the_way:
        later = listed([c.short_sha for c in conflict.in_the_way])
        why = (
            f"its change to {files} conflicts with what {later} change there after it"
            if later
            else f"it conflicts in {files}"
        )
    elif stop.kind is PickStop.EMPTY:
        why = f'once it is in, {stop.commit.short_sha} "{stop.commit.subject}" changes nothing any more'
    else:
        why = f'once it is in, {stop.commit.short_sha} "{stop.commit.subject}" no longer applies (conflict in {files})'
    again = " ".join(["ger fix --force", *(["-a"] if args.commit_all else []), args.target])
    return (
        f'this fixup would not fold cleanly into {target.short_sha} "{target.subject}": {why}. '
        f"Make the change in that commit itself (`ger edit {target.short_sha}`), "
        f"or create the fixup anyway (`{again}`) and resolve the conflict at `ger rebase --autosquash`"
    )


def _fold_now(cwd: Path, target: Commit, made: str, *, settings: Settings) -> str:
    """Fold the fixup just *made* (HEAD) into *target*, as the autosquash would: the outcome line."""
    try:
        rewrite, folded = fold_fixup(cwd, target, settings=settings)
    except ReorderRefusal as exc:
        raise UsageError(f"made {made}, but did not fold it: {exc}") from exc
    above = counted(len(rewrite.commits) - rewrite.commits.index(folded) - 1, "commit")
    stash = (
        "; your uncommitted changes did not go back on cleanly: they are in `git stash`" if rewrite.stash_kept else ""
    )
    return f"folded into {folded.short_sha} {folded.subject} ({above} above it rewritten){stash}"


if __name__ == "__main__":
    raise SystemExit(main())
