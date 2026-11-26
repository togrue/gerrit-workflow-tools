"""CLI for pushing one commit or a stack to Gerrit."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable
from pathlib import Path

from gerrit_workflow_tools.cli_args import (
    add_branch_arg,
    add_color_args,
    add_dry_run_arg,
    add_follow_merges_args,
    add_verbose_and_debug_log_args,
    add_yes_arg,
)
from gerrit_workflow_tools.cli_attention import resolve_in_stack
from gerrit_workflow_tools.cli_common import ExitCode, init_cli_runtime, require_prompt, run_cli_command
from gerrit_workflow_tools.cli_terminal import assume_yes, confirm, stdin_is_terminal
from gerrit_workflow_tools.core.gerrit.comments import PUBLISH_GUARD_ENV, publish_blocked
from gerrit_workflow_tools.core.gerrit.rest import GerritRest
from gerrit_workflow_tools.core.git_run import commit_sha_or_none
from gerrit_workflow_tools.core.git_state import checked_out_branch_name, ger_push_mode
from gerrit_workflow_tools.core.push import PushPlan, PushRefusal, changes_nothing, execute, prepare, prepare_vanilla
from gerrit_workflow_tools.core.push_checks import PushBoundary, add_change_ids_for_push, resolve_push_branch
from gerrit_workflow_tools.core.review_checkout import staged_review_refusal
from gerrit_workflow_tools.core.reviewer import ReviewerStrategy
from gerrit_workflow_tools.core.reword import take_back
from gerrit_workflow_tools.core.run_context import RunContext
from gerrit_workflow_tools.core.step_outcome import record_outcome, refusal_recorded
from gerrit_workflow_tools.core.upstream import gone_upstream, missing_upstream_error
from gerrit_workflow_tools.core.usage import UsageError
from gerrit_workflow_tools.core.words import counted
from gerrit_workflow_tools.push_confirm import confirm_gerrit_push, prompt_initial_options
from gerrit_workflow_tools.push_outcome import finish_and_record, finish_push, print_refusal
from gerrit_workflow_tools.push_preview import print_nothing_to_push
from gerrit_workflow_tools.render.style import ANSI_DIM_GRAY, color_text
from gerrit_workflow_tools.summary_highlight import branch_summary_highlighter
from gerrit_workflow_tools.upstream_interactive import require_branch_upstream


logger = logging.getLogger(__name__)


def _build_arg_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser for ``ger push``."""
    parser = argparse.ArgumentParser(prog="ger push")
    parser.add_argument(
        "-i",
        action="store_true",
        help=(
            "Prompt for reviewers (TTY only; non-empty line overwrites branch and --reviewers; "
            "cannot be used with --yes)."
        ),
    )
    add_branch_arg(parser)
    add_dry_run_arg(parser)
    parser.add_argument(
        "--no-rebase-check",
        action="store_true",
        help="Do not fetch or verify that HEAD is linear on the remote Gerrit target (see ger.pushRemotePolicy).",
    )
    add_yes_arg(parser)
    parser.add_argument(
        "--publish-comments",
        action="store_true",
        help="Publish your drafts on the updated changes with the push (needed with -y; the prompt asks).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Push even over a patchset that was uploaded from elsewhere and is not in your history (diverged).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        dest="all_",
        help="Push the full stack (ignore stop patterns).",
    )
    add_color_args(parser)
    add_follow_merges_args(parser)
    parser.add_argument(
        "--reviewers",
        action="append",
        default=[],
        metavar="ACCOUNTS",
        help=(
            "Comma-separated Gerrit reviewer accounts (repeat for more; overwrites branch gerReviewers). "
            "Appended as ref options (%%r=…)."
        ),
    )
    parser.add_argument(
        "--no-chain-reviewers",
        action="store_true",
        help=(
            "A change the push creates in a chain whose other changes have reviewers gets nobody "
            "(default: the chain's reviewers, bots left out, when the push names none; ger.pushChainReviewers)."
        ),
    )
    parser.add_argument(
        "--reviewer-strategy",
        choices=[strategy.value for strategy in ReviewerStrategy],
        default=None,
        help=(
            "How to apply reviewers when pushing: push (%%r= on ref), lazy (REST: add only where none), "
            "overwrite (REST: replace reviewers on each change; CC stays). Requires credentials for lazy/overwrite. "
            "Changes Gerrit already has at that commit get topic/WIP/ready/private/reviewers over REST."
        ),
    )
    parser.add_argument(
        "--topic",
        metavar="NAME",
        default=None,
        help="Gerrit change topic (magic ref %%topic=…; REST where Gerrit already has the commit).",
    )
    # Each group's flags are added one after the other, so the usage line brackets them: [--wip | --ready | ...].
    state = parser.add_mutually_exclusive_group()
    _add_mark_flags(state, ("--wip", "work-in-progress (magic ref %%wip"))
    _add_mark_flags(state, ("--ready", "ready for review, the way back from --wip (magic ref %%ready"))
    state.add_argument(
        "--wip-from",
        metavar="REV",
        default=None,
        help="Push the commits from REV up as work-in-progress and those below it ready (two pushes). "
        "--all does this at the ready boundary by itself.",
    )
    _add_mark_flags(
        parser.add_mutually_exclusive_group(),
        ("--private", "private (magic ref %%private"),
        ("--no-private", "not private, the way back from --private (magic ref %%remove-private"),
    )
    add_verbose_and_debug_log_args(
        parser,
    )
    parser.add_argument(
        "until",
        nargs="?",
        default=None,
        metavar="REV",
        help="Push only through this commit: a git rev, Change-Id, change:N or triplet in the local stack.",
    )
    return parser


def _add_mark_flags(group: argparse._MutuallyExclusiveGroup, *flags: tuple[str, str]) -> None:
    for flag, what in flags:
        group.add_argument(
            flag, action="store_true", help=f"Mark the changes {what}; REST where Gerrit has the commit)."
        )


def _gerrit_flags_set(args: argparse.Namespace) -> bool:
    return bool(
        args.until
        or args.all_
        or args.reviewers
        or args.topic is not None
        or args.wip
        or args.wip_from
        or args.ready
        or args.private
        or args.no_private
        or args.reviewer_strategy is not None
    )


def _handle_vanilla_push(run: RunContext, args: argparse.Namespace, *, branch: str) -> int:
    """Handle the vanilla (non-Gerrit) ``git push`` flow and return an exit code."""
    cwd, settings = run.cwd, run.settings
    if _gerrit_flags_set(args):
        print(
            "warning: --until, --all, --reviewers, --topic, --wip, --ready, --private, --no-private "
            "and --reviewer-strategy apply only to Gerrit push; ignoring.",
            file=sys.stderr,
        )
    prepared = prepare_vanilla(
        cwd,
        branch,
        settings=settings,
        explicit_branch=args.branch is not None,
        checked_out=checked_out_branch_name(cwd),
    )
    if isinstance(prepared, PushRefusal):
        print(prepared.message, file=sys.stderr)
        return int(ExitCode.REFUSED)
    cmd = prepared
    logger.debug("ger push vanilla: %s", cmd)
    if args.dry_run:
        print(color_text(" ".join(cmd), ANSI_DIM_GRAY))
        print("[dry-run] not executing push", file=sys.stderr)
        return int(ExitCode.OK)
    if not args.yes:
        require_prompt(args, instead="-y to push")
        print(color_text(" ".join(cmd), ANSI_DIM_GRAY))
        print()
        if not confirm("Do you want to run `git push`?", default=True):
            print("Push cancelled.", file=sys.stderr)
            return int(ExitCode.OK)  # your choice: the command did what you asked (exit-codes.md)
    logger.debug("ger push vanilla executing: %s (cwd=%s)", " ".join(cmd), cwd)
    return finish_push(execute(run, cmd), None)


def _push_gerrit(run: RunContext, args: argparse.Namespace, *, branch: str) -> int:
    cwd, settings = run.cwd, run.settings
    defaults = settings.push_defaults
    first_parent = not args.follow_merges

    def prepare_push() -> PushPlan | PushRefusal:
        """The push as it stands; asked again once Change-Ids were added (new commits, --until resolved anew)."""
        return prepare(
            run,
            branch,
            all_commits=bool(args.all_),
            until=args.until,
            until_sha=args.until and resolve_in_stack(run, args.until, branch=branch).sha,
            first_parent=first_parent,
            reviewer_flags=tuple(args.reviewers),
            strategy=args.reviewer_strategy,
            topic=args.topic,
            wip=bool(args.wip),
            ready_for_review=bool(args.ready),
            private=bool(args.private),
            remove_private=bool(args.no_private),
            no_rebase_check=bool(args.no_rebase_check),
            show_attributes=defaults["show_attributes"],
            update_last_pushed=defaults["last_pushed_branch"],
            publish_comments=bool(args.publish_comments),
            force=bool(args.force),
            wip_from=args.wip_from and resolve_in_stack(run, args.wip_from, branch=branch).sha,
            chain_reviewers=defaults["chain_reviewers"] and not args.no_chain_reviewers,
        )

    prepared = prepare_push()
    # No commit-msg hook adds Change-Ids (ger setup installs none): the push adds the missing ones,
    # as the hook would have at commit time, says so before its preview, and goes on with them. They
    # stay only once the push is sent: a push not sent takes them back (`_ChangeIds.take_back`).
    added = _ChangeIds(cwd)
    if args.dry_run and prepared.boundary is not None and prepared.boundary.missing_change_id:
        print("ger push (without --dry-run) adds the missing Change-Ids first, and pushes those commits too.")
    elif added.add(branch, prepared.boundary):
        prepared = prepare_push()
    try:
        return _confirm_and_push(run, args, prepared, prepare_push, added, branch=branch)
    finally:
        added.take_back()


class _ChangeIds:
    """The Change-Ids a push added for its preview: kept once it is sent, else taken back."""

    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd
        self.count = 0
        self.before: str | None = None
        self.after: str | None = None
        self.sent = False

    def add(self, branch: str, boundary: PushBoundary | None) -> bool:
        before = commit_sha_or_none(self.cwd, "HEAD")
        added = add_change_ids_for_push(self.cwd, branch, boundary)
        if not added:
            return False
        self.count, self.before, self.after = len(added), before, commit_sha_or_none(self.cwd, "HEAD")
        named = ", ".join(f'"{commit.subject}"' for commit in added)
        print(
            f"Adds a Change-Id to {counted(len(added), 'commit')}, as a commit-msg hook would: {named} "
            "(kept only if you push)"
        )
        return True

    def take_back(self) -> str:
        """Put the branch back where it was, unless the push was sent; what a push not sent says of it."""
        if self.sent or not (self.before and self.after):
            return ""
        before, self.before = self.before, None
        if not take_back(self.cwd, before, self.after, why="ger push: not sent, Change-Ids taken back"):
            return ""
        said = f"the Change-Ids added to {counted(self.count, 'commit')} are taken back"
        print(f"{said[0].upper()}{said[1:]}: your commits are as before.")
        return f"; {said}"


def _confirm_and_push(
    run: RunContext,
    args: argparse.Namespace,
    prepared: PushPlan | PushRefusal,
    prepare_push: Callable[[], PushPlan | PushRefusal],
    added: _ChangeIds,
    *,
    branch: str,
) -> int:
    """The preview, the question and the push, each way it can end left as the **step outcome**."""
    cwd, settings = run.cwd, run.settings
    first_parent = not args.follow_merges
    if isinstance(prepared, PushRefusal):
        print_refusal(cwd, branch, prepared, first_parent=first_parent, settings=settings)
        note = added.take_back()  # first: the outcome names HEAD as it is left
        record_outcome(cwd, "push", f"refused: {prepared.message.removeprefix('error: ')}{note}", ok=False)
        return int(ExitCode.REFUSED)
    for warning in prepared.warnings:
        print(warning, file=sys.stderr)
    asked = prompt_initial_options(cwd, prepared, args, settings=settings) if args.i else prepared
    if asked is None:
        print("Push cancelled.", file=sys.stderr)
        note = added.take_back()  # first: the outcome names HEAD as it is left
        record_outcome(cwd, "push", f"push cancelled: nothing sent{note}", ok=False)
        return int(ExitCode.OK)
    plan = asked
    highlighter = branch_summary_highlighter(cwd, plan.branch, first_parent=first_parent, settings=settings)
    if changes_nothing(plan) and not args.dry_run:  # nothing to confirm, so -y is not needed either
        print_nothing_to_push(plan, summary_highlighter=highlighter)
        return finish_and_record(cwd, execute(run, plan), plan)
    confirmed = confirm_gerrit_push(run, plan, args, summary_highlighter=highlighter)
    if isinstance(confirmed, int):
        if not args.dry_run:
            note = added.take_back()  # first: the outcome names HEAD as it is left
            record_outcome(cwd, "push", f"push cancelled: nothing sent{note}", ok=False)
        return confirmed
    added.sent = True
    code = finish_and_record(cwd, execute(run, confirmed), confirmed)
    if code == 0 and confirmed.options != prepared.options:
        from gerrit_workflow_tools.push_options_history import remember_push_options

        remember_push_options(cwd, confirmed, settings=settings)
    return code


def main(argv: list[str] | None = None, *, gerrit: GerritRest | None = None) -> int:
    """CLI entry for ``ger push``: compute ready range and push to Gerrit."""
    return run_cli_command(lambda: _run(argv, gerrit=gerrit))


def _run(argv: list[str] | None, *, gerrit: GerritRest | None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    run = init_cli_runtime(args, gerrit=gerrit)
    cwd, settings = run.cwd, run.settings
    logger.debug(
        "ger push cwd=%s branch=%s dry_run=%s yes=%s all=%s until=%s i=%s remote_policy=%s "
        "no_rebase_check=%s follow_merges=%s reviewer_strategy=%s",
        cwd,
        args.branch,
        args.dry_run,
        args.yes,
        args.all_,
        args.until,
        args.i,
        settings.push_remote_policy,
        args.no_rebase_check,
        args.follow_merges,
        args.reviewer_strategy,
    )
    if args.i and args.yes:
        parser.error("-i cannot be used with --yes (-y)")
    args.yes = args.yes or (assume_yes() and not args.i)
    if args.publish_comments and publish_blocked():
        parser.error(f"--publish-comments is blocked: {PUBLISH_GUARD_ENV} is set in the environment")
    if args.i and not stdin_is_terminal():
        raise UsageError("-i requires an interactive terminal (stdin is not a TTY)")
    with refusal_recorded(cwd, "push"):
        return _push(run, args)


def _push(run: RunContext, args: argparse.Namespace) -> int:
    cwd, settings = run.cwd, run.settings

    # A staged review makes its change-<n> the working branch: a push without --branch would send it.
    if not args.branch and (refusal := staged_review_refusal(run.worktree.repository_dir, cwd)):
        raise UsageError(refusal)
    detached = run.worktree.branch is None
    branch = resolve_push_branch(run, args.branch)
    mode = ger_push_mode(cwd, branch, settings=settings)
    if not args.yes and not args.dry_run and mode in ("gerrit", None) and (args.branch or not detached):
        if not require_branch_upstream(cwd, branch, settings=settings):
            record_outcome(cwd, "push", f"refused: {branch} has no upstream to push to", ok=False)
            return int(ExitCode.REFUSED)
        mode = ger_push_mode(cwd, branch, settings=settings)
    if mode is None:  # Already asked above, or chose not to (--yes, --dry-run, detached HEAD).
        raise missing_upstream_error(branch, askable=False, gone=gone_upstream(cwd, branch))
    if args.i and mode == "vanilla":
        raise UsageError("-i applies only to Gerrit push (upstream on ger.remote)")
    if mode == "vanilla":
        return _handle_vanilla_push(run, args, branch=branch)
    return _push_gerrit(run, args, branch=branch)


if __name__ == "__main__":
    raise SystemExit(main())
