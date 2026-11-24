"""CLI for pushing one commit or a stack to Gerrit."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from gerrit_workflow_tools.cli_attention import resolve_in_stack
from gerrit_workflow_tools.cli_common import (
    add_color_args,
    add_follow_merges_args,
    add_verbose_and_debug_log_args,
    assume_yes,
    confirm,
    init_cli_runtime,
    run_cli_command,
)
from gerrit_workflow_tools.cli_terminal import stdin_is_terminal
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.comments import PUBLISH_GUARD_ENV, publish_blocked
from gerrit_workflow_tools.core.gerrit.rest import GerritRest
from gerrit_workflow_tools.core.git_state import checked_out_branch_name, ger_push_mode
from gerrit_workflow_tools.core.push import (
    PushPlan,
    PushRefusal,
    changes_nothing,
    execute,
    prepare,
    prepare_vanilla,
    publish_drafts_with,
    publishable_drafts,
    push_argv,
    sent_commits,
)
from gerrit_workflow_tools.core.push_checks import resolve_push_branch
from gerrit_workflow_tools.core.reviewer import ReviewerStrategy
from gerrit_workflow_tools.core.run_context import RunContext
from gerrit_workflow_tools.core.upstream import missing_upstream_error
from gerrit_workflow_tools.core.usage import UsageError
from gerrit_workflow_tools.push_outcome import finish_push, print_refusal
from gerrit_workflow_tools.push_preview import (
    apply_interactive_line,
    ask_push_action,
    confirm_with_options_line,
    options_line_available,
    print_dry_run,
    print_gpush_preview,
    print_plan_status,
    prompt_initial_options,
)
from gerrit_workflow_tools.render.style import ANSI_DIM_GRAY, color_text
from gerrit_workflow_tools.summary_highlight import SummaryHighlighter, branch_summary_highlighter
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
    parser.add_argument(
        "--branch",
        metavar="NAME",
        default=None,
        help="Push the specified local branch instead of the current branch.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print actions only; do not push.")
    parser.add_argument(
        "--no-rebase-check",
        action="store_true",
        help="Do not fetch or verify that HEAD is linear on the remote Gerrit target (see ger.pushRemotePolicy).",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Push without confirmation (required when stdin is not a terminal; GER_ASSUME_YES=1 sets it).",
    )
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
            "Appended as ref options %%r=…"
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
    state, private = parser.add_mutually_exclusive_group(), parser.add_mutually_exclusive_group()
    for group, flag, what in (
        (state, "--wip", "work-in-progress (magic ref %%wip"),
        (state, "--ready", "ready for review, the way back from --wip (magic ref %%ready"),
        (private, "--private", "private (magic ref %%private"),
        (private, "--no-private", "not private, the way back from --private (magic ref %%remove-private"),
    ):
        group.add_argument(flag, action="store_true", help=f"Mark change(s) {what}; REST where Gerrit has the commit).")
    state.add_argument(
        "--wip-from",
        metavar="REV",
        default=None,
        help="Push the commits from REV up as work-in-progress and those below it ready (two pushes). "
        "--all does this at the ready boundary by itself.",
    )
    add_verbose_and_debug_log_args(
        parser,
        debug_log_help="Log git commands and push steps to stderr.",
    )
    parser.add_argument(
        "until",
        nargs="?",
        default=None,
        metavar="REV",
        help="Push only through this commit: a git rev, Change-Id, change:N or triplet in the local stack.",
    )
    return parser


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


def _require_confirmable(args: argparse.Namespace) -> None:
    if not (stdin_is_terminal() or args.yes):
        raise UsageError("non-interactive stdin: use --yes (-y) to push without a confirmation prompt")


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
        return 1
    cmd = prepared
    logger.debug("gpush vanilla: %s", cmd)
    if args.dry_run:
        print(color_text(" ".join(cmd), ANSI_DIM_GRAY))
        print("[dry-run] not executing push", file=sys.stderr)
        return 0
    _require_confirmable(args)
    if not args.yes:
        print(color_text(" ".join(cmd), ANSI_DIM_GRAY))
        print()
        if not confirm("Do you want to run `git push`?", default=True):
            print("Push cancelled.", file=sys.stderr)
            return 1
    logger.debug("gpush vanilla executing: %s (cwd=%s)", " ".join(cmd), cwd)
    return finish_push(execute(run, cmd), None)


def _confirm_gerrit_push(
    cwd: Path,
    plan: PushPlan,
    args: argparse.Namespace,
    *,
    settings: Settings,
    summary_highlighter: SummaryHighlighter,
) -> PushPlan | int:
    """Return the plan to execute, or an exit code when the command should stop."""
    stack_printed = False
    drafts_defaulted = False
    while True:
        argv = push_argv(plan)
        logger.debug(
            "gpush resolved: remote=%r push_branch=%r reviewers=%s strategy=%s refspec=%r",
            plan.remote,
            plan.push_branch,
            plan.options.reviewers,
            plan.options.strategy,
            argv[-1],
        )
        if not stack_printed:
            print_gpush_preview(plan, summary_highlighter=summary_highlighter)
            stack_printed = True
        if args.dry_run:
            print_dry_run(plan)
            return 0
        _require_confirmable(args)
        if args.yes:
            if plan.credential_error:
                print(plan.credential_error, file=sys.stderr)
                return 1
            return plan
        line = options_line_available()
        drafts = publishable_drafts(plan)
        if line and drafts and not drafts_defaulted:  # the line starts with publish-drafts unless config says no
            drafts_defaulted = True
            if settings.push_defaults["publish_drafts"]:
                plan = publish_drafts_with(plan, True)
        print()
        print_plan_status(plan)
        print()
        if line:
            revised = confirm_with_options_line(cwd, plan, args, settings=settings, drafts=drafts)
            act = "push" if revised else "cancel"
            plan = revised or plan
        else:
            asked = drafts if plan.options.publish_comments is False else 0
            act = ask_push_action(asked, sends=bool(sent_commits(plan)))
            if act == "push" and asked:
                plan = publish_drafts_with(plan, True)
        if act == "cancel":
            print("Push cancelled.", file=sys.stderr)
            return 0
        if act == "reviewers":
            revised = apply_interactive_line(cwd, plan, args, settings=settings, save=False)
            if revised is not None:
                plan = revised
            continue
        if plan.credential_error:
            print(plan.credential_error, file=sys.stderr)
            continue
        return plan


def _push_gerrit(run: RunContext, args: argparse.Namespace, *, branch: str) -> int:
    cwd, settings = run.cwd, run.settings
    defaults = settings.push_defaults
    first_parent = not args.follow_merges
    prepared = prepare(
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
    )
    if isinstance(prepared, PushRefusal):
        print_refusal(cwd, branch, prepared, first_parent=first_parent, settings=settings)
        return 1
    for warning in prepared.warnings:
        print(warning, file=sys.stderr)
    plan = prepared
    if args.i:
        plan = prompt_initial_options(cwd, plan, args, settings=settings)
    if changes_nothing(plan) and not args.dry_run:  # nothing to confirm, so -y is not needed either
        return finish_push(execute(run, plan), plan)
    highlighter = branch_summary_highlighter(cwd, plan.branch, first_parent=first_parent, settings=settings)
    confirmed = _confirm_gerrit_push(cwd, plan, args, settings=settings, summary_highlighter=highlighter)
    if isinstance(confirmed, int):
        return confirmed
    code = finish_push(execute(run, confirmed), confirmed)
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
        "gpush cwd=%s branch=%s dry_run=%s yes=%s all=%s until=%s i=%s remote_policy=%s "
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

    detached = run.worktree.branch is None
    branch = resolve_push_branch(run, args.branch)
    mode = ger_push_mode(cwd, branch, settings=settings)
    if not args.yes and not args.dry_run and mode in ("gerrit", None) and (args.branch or not detached):
        if not require_branch_upstream(cwd, branch, settings=settings):
            return 1
        mode = ger_push_mode(cwd, branch, settings=settings)
    if mode is None:  # Already asked above, or chose not to (--yes, --dry-run, detached HEAD).
        raise missing_upstream_error(branch, askable=False)
    if args.i and mode == "vanilla":
        raise UsageError("-i applies only to Gerrit push (upstream on ger.remote)")
    if mode == "vanilla":
        return _handle_vanilla_push(run, args, branch=branch)
    return _push_gerrit(run, args, branch=branch)


if __name__ == "__main__":
    raise SystemExit(main())
