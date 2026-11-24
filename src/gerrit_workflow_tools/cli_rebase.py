"""CLI entry: ``ger rebase`` — interactive rebase with Gerrit status annotations."""

from __future__ import annotations

import argparse
import logging

from gerrit_workflow_tools.cli_common import (
    ExitCode,
    add_verbose_and_debug_log_args,
    changeish_help,
    init_cli_runtime,
    run_cli_command,
)
from gerrit_workflow_tools.cli_notes import print_resolution_note
from gerrit_workflow_tools.cli_terminal import stdin_is_terminal
from gerrit_workflow_tools.core.gerrit.change_resolution import resolve_stack_changeish
from gerrit_workflow_tools.core.gerrit.rest import GerritRest
from gerrit_workflow_tools.core.git_state import resolve_rebase_onto_remote_ref
from gerrit_workflow_tools.core.rebase_state import RebaseAction
from gerrit_workflow_tools.core.stack import merge_base_with_target
from gerrit_workflow_tools.rebase_run import go_on_with_rebase, require_history_rewrite, run_interactive_rebase
from gerrit_workflow_tools.upstream_interactive import require_branch_upstream


logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the command-line parser for ``ger rebase``."""
    p = argparse.ArgumentParser(
        prog="ger rebase",
        description=(
            "Start an interactive rebase with Gerrit status annotations in the sequence editor.\n\n"
            "Each pick line is enriched with the commit's patchset status, Verified and Code-Review\n"
            "labels, unresolved comment count, and an actionable attention note.\n\n"
            "Without a terminal (scripts, agents) no editor opens: the todo runs as annotated, so\n"
            "`ger rebase --autosquash` squashes the fixups `ger fix` made and `--onto-remote\n"
            "--drop-merged-equivalent` drops commits Gerrit has merged, unattended.\n\n"
            "--continue, --skip and --abort go on with the rebase underway, as git's own options do,\n"
            "and say what they did: the commits rewritten, a commit dropped and how to get it back."
        ),
    )
    p.add_argument(
        "rev",
        metavar="REV",
        nargs="?",
        default=None,
        help=changeish_help(
            "The base to rebase from (default: the merge base with the target; not with --onto-remote)"
        ),
    )
    p.add_argument(
        "--onto-remote",
        action="store_true",
        help=(
            "Rebase the current branch onto the fetched remote-tracking tip of the upstream "
            "target on ger.remote. Default: ger.rebaseOntoRemote."
        ),
    )
    p.add_argument(
        "--no-onto-remote",
        action="store_true",
        help="Override ger.rebaseOntoRemote: use merge-base with the target (default behavior).",
    )
    p.add_argument(
        "--drop-merged-equivalent",
        action="store_true",
        help=(
            "Turn merged commits that are provably equivalent to the merged Gerrit revision into "
            "drop lines in the todo. Default: ger.rebaseDropMergedEquivalent."
        ),
    )
    p.add_argument(
        "--autosquash",
        action="store_true",
        help=(
            "Squash the fixups `ger fix` made (fixup!/squash! commits) into the commits they fix. "
            "Without it, git's rebase.autosquash decides, as for `git rebase -i`."
        ),
    )
    underway = p.add_mutually_exclusive_group()
    underway.add_argument(
        "--continue",
        dest="go_on",
        action="store_const",
        const="continue",
        help=(
            "Finish the rebase underway, or go on to its next stop, once the commit it stopped at is "
            "amended (`git commit --amend`) or its conflicts are resolved and added (`git add`)."
        ),
    )
    underway.add_argument(
        "--abort",
        dest="go_on",
        action="store_const",
        const="abort",
        help="Undo the rebase underway: the branch goes back to where it was before it started.",
    )
    underway.add_argument(
        "--skip",
        dest="go_on",
        action="store_const",
        const="skip",
        help=(
            "Leave out the commit the rebase underway stopped on with a conflict, and go on without it; "
            "it says which commit it dropped and how to get it back. "
            "Refused at any other stop, where git would only throw away uncommitted changes."
        ),
    )
    add_verbose_and_debug_log_args(
        p,
        debug_log_help="Log git commands and enricher steps to stderr.",
    )
    return p


def main(argv: list[str] | None = None, *, gerrit: GerritRest | None = None) -> int:
    """CLI entry for ``ger rebase``: interactive rebase with Gerrit status annotations.

    Sets ``GIT_SEQUENCE_EDITOR`` to the enricher wrapper
    (:mod:`gerrit_workflow_tools.rebase_enricher`) which annotates each pick line with
    the commit's Gerrit verified/CR/comments status before opening the real editor.

    The real editor is resolved by the enricher from ``GREBASE_EDITOR``, ``GIT_EDITOR``,
    ``core.editor``, ``VISUAL``, or ``EDITOR`` — no extra configuration needed.
    """
    return run_cli_command(lambda: _run(argv, gerrit=gerrit))


def _run(argv: list[str] | None, *, gerrit: GerritRest | None) -> int:
    p = _build_parser()
    args = p.parse_args(argv)
    run = init_cli_runtime(args, gerrit=gerrit)
    cwd, settings = run.cwd, run.settings

    if args.go_on:
        if args.rev or args.onto_remote or args.no_onto_remote or args.drop_merged_equivalent or args.autosquash:
            p.error(f"--{args.go_on} goes on with the rebase underway: it takes no REV and no other option")
        return go_on_with_rebase(cwd, RebaseAction(args.go_on))
    if args.onto_remote and args.rev:
        p.error("cannot combine --onto-remote with a REV argument")

    require_history_rewrite(cwd)
    rdef = settings.rebase_defaults
    if args.no_onto_remote:
        use_onto_remote = False
    elif args.onto_remote:
        use_onto_remote = True
    elif args.rev:
        use_onto_remote = False
    else:
        use_onto_remote = rdef["onto_remote"]

    drop_merged = bool(args.drop_merged_equivalent or rdef["drop_merged_equivalent"])

    branch = run.repo.working_branch
    if use_onto_remote:
        base = resolve_rebase_onto_remote_ref(cwd, branch, settings=settings)
    elif args.rev:
        # Same changeish resolution as `ger fix` / `ger edit`, but without require_in_stack:
        # this argument is the commit to rebase *from*, which normally sits below the stack.
        target = resolve_stack_changeish(cwd, args.rev, service=run.gerrit, branch=branch)
        base = target.sha
        print_resolution_note(target.resolution)
    else:
        if branch is not None and not require_branch_upstream(cwd, branch, settings=settings):
            return int(ExitCode.REFUSED)
        base, _, _ = merge_base_with_target(cwd, branch)

    # The enricher reads GREBASE_EDITOR / GIT_EDITOR / core.editor / EDITOR to find the editor to open.
    env: dict[str, str] = {}
    if args.debug_log:
        env["GREBASE_DEBUG_LOG"] = "1"
    if drop_merged:
        env["GREBASE_DROP_MERGED_EQUIVALENT"] = "1"
    if not stdin_is_terminal():
        env["GIT_EDITOR"] = "true"  # nobody to edit the todo: run it as annotated

    logger.debug("ger rebase: base=%s onto_remote=%s", base[:8], use_onto_remote)
    autosquash = True if args.autosquash else None
    return run_interactive_rebase(cwd, base, sequence_editor="rebase_enricher", env=env, autosquash=autosquash)


if __name__ == "__main__":
    raise SystemExit(main())
