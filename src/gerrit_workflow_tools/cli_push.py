"""CLI for pushing one commit or a stack to Gerrit."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from gerrit_workflow_tools.cli_common import (
    add_color_args,
    add_follow_merges_args,
    add_verbose_and_debug_log_args,
    ask_choice,
    confirm,
    init_cli_runtime,
    run_cli_command,
)
from gerrit_workflow_tools.cli_style import ANSI_DIM_GRAY, color_text
from gerrit_workflow_tools.core.change_id import extract_valid_change_id
from gerrit_workflow_tools.core.config import Settings, set_branch_config
from gerrit_workflow_tools.core.gerrit.rest import GerritRest
from gerrit_workflow_tools.core.gerrit.stack_context import stack_project
from gerrit_workflow_tools.core.git_run import GitError, git_out
from gerrit_workflow_tools.core.git_state import (
    checked_out_branch_name,
    ger_push_mode,
    is_detached_head,
    resolve_working_branch,
)
from gerrit_workflow_tools.core.push import (
    PushOptions,
    PushPlan,
    PushRefusal,
    PushResult,
    execute,
    prepare,
    prepare_vanilla,
    push_argv,
    revise,
)
from gerrit_workflow_tools.core.push_reviewers import needs_rest_assignment
from gerrit_workflow_tools.core.reviewer import ReviewerStrategy, parse_reviewer_names, resolve_push_reviewers
from gerrit_workflow_tools.core.stack import get_stack_snapshot
from gerrit_workflow_tools.core.upstream_interactive import require_branch_upstream, stdin_is_terminal
from gerrit_workflow_tools.push_input_line import ParseResult, PushLineState, format_canonical
from gerrit_workflow_tools.push_preview import (
    commit_lines,
    print_assignment_report,
    print_gpush_preview,
    print_plan_status,
    print_ready_boundary_notice,
    strategy_status_label,
)
from gerrit_workflow_tools.summary_highlight import SummaryHighlighter, build_summary_highlighter


logger = logging.getLogger(__name__)


def _reviewer_seeds_for_prompt(branch: str, *, settings: Settings) -> list[str]:
    cfg = settings.branch_gerrit_reviewers(branch)
    return parse_reviewer_names(cfg) if cfg else []


def _push_options_history_identity(
    cwd: Path | None,
    *,
    settings: Settings,
) -> tuple[str | None, str | None]:
    """Return ``(web_base, project)`` for push-options history, or ``(None, None)``."""
    web_base = settings.gerrit_web_url
    project = stack_project(cwd, settings=settings) if cwd is not None else settings.gerrit_project
    if not web_base or not project:
        return None, None
    return web_base, project


def _synthesize_push_options_default(
    cwd: Path,
    branch: str,
    reviewer_flag_segments: list[str],
    *,
    settings: Settings,
) -> str:
    """Build an empty-history prefill from ``--reviewers`` / registry / branch config."""
    reviewers = resolve_push_reviewers(cwd, branch, reviewer_flag_segments, settings=settings)
    if not reviewers:
        return ""
    return f"r={','.join(reviewers)}"


def _change_id_for_rev(cwd: Path, rev: str) -> str | None:
    try:
        msg = git_out("show", "-s", "--format=%B", rev, cwd=cwd)
    except GitError:
        return None
    return extract_valid_change_id(msg)


def _prompt_save_reviewers() -> bool:
    return confirm("Save reviewers to branch config?", default=False)


def _prompt_reviewers_line_ptk(
    cwd: Path | None = None,
    branch: str | None = None,
    *,
    settings: Settings,
    change_id_hint: str | None = None,
    reviewer_flag_segments: list[str] | None = None,
    session_strategy: ReviewerStrategy | None = None,
) -> ParseResult:
    """Confirm-loop ``r`` action: open the highlighted push-options input line."""
    from gerrit_workflow_tools.push_input_prompt import prompt_push_options_line

    seeds = _reviewer_seeds_for_prompt(branch, settings=settings) if branch is not None else []
    web_base, project = _push_options_history_identity(cwd, settings=settings)
    empty_default = ""
    if cwd is not None and branch is not None:
        empty_default = _synthesize_push_options_default(cwd, branch, reviewer_flag_segments or [], settings=settings)
    return prompt_push_options_line(
        reviewer_seeds=seeds,
        message="Push options: ",
        cwd=cwd,
        settings=settings,
        change_id_hint=change_id_hint,
        web_base=web_base,
        project=project,
        default=empty_default,
        session_strategy=session_strategy.value if session_strategy is not None else None,
    )


def _prompt_gerrit_push_confirm_action() -> str:
    return ask_choice(
        "Do you want to push these commits? [Y/n/r]: ",
        {
            "": "push",
            "y": "push",
            "yes": "push",
            "n": "cancel",
            "no": "cancel",
            "r": "reviewers",
            "reviewer": "reviewers",
            "reviewers": "reviewers",
        },
        hint="  (Enter or y: push, n: cancel, r: set reviewers and strategy)",
    )


def _summary_highlighter_for_push(
    cwd: Path,
    *,
    branch: str,
    first_parent: bool,
    settings: Settings,
) -> SummaryHighlighter:
    """Highlighter aligned with the ready boundary for *branch*'s local stack."""
    snap = get_stack_snapshot(cwd, branch, head=branch, first_parent=first_parent, settings=settings)
    return build_summary_highlighter(
        settings,
        cwd=cwd,
        commits=list(snap.commits),
        project=stack_project(cwd, branch, settings=settings),
        web_base=settings.gerrit_web_url,
    )


def _line_sets_options(state: PushLineState) -> bool:
    return bool(
        state.reviewers or state.topic or state.wip or state.private or state.strategy != ReviewerStrategy.PUSH.value
    )


def _options_from_line(state: PushLineState) -> PushOptions:
    return PushOptions(
        reviewers=tuple(state.reviewers),
        strategy=ReviewerStrategy(state.strategy),
        topic=state.topic,
        wip=state.wip,
        private=state.private,
    )


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
        help="Do not fetch or verify that HEAD is linear on the remote Gerrit target (see gerrit.push.remotePolicy).",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Push without confirmation (required when stdin is not a terminal).",
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
            "Comma-separated Gerrit reviewer accounts (repeat for more; overwrites branch gerritReviewers). "
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
            "Topic/WIP/private always use magic ref options (see --topic / --wip / --private)."
        ),
    )
    parser.add_argument(
        "--topic",
        metavar="NAME",
        default=None,
        help="Gerrit change topic (magic ref %%topic=…; sent on push for every reviewer strategy).",
    )
    parser.add_argument(
        "--wip",
        action="store_true",
        help="Mark change(s) work-in-progress (magic ref %%wip; sent on push for every reviewer strategy).",
    )
    parser.add_argument(
        "--private",
        action="store_true",
        help="Mark change(s) private (magic ref %%private; sent on push for every reviewer strategy).",
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
        help="Push only through this commit.",
    )
    return parser


def _gerrit_flags_set(args: argparse.Namespace) -> bool:
    return bool(
        args.until
        or args.all_
        or args.reviewers
        or args.topic is not None
        or args.wip
        or args.private
        or args.reviewer_strategy is not None
    )


def _emit_push_output(result: PushResult) -> None:
    for stream, text in ((sys.stdout, result.stdout), (sys.stderr, result.stderr)):
        if not text:
            continue
        stream.write(text)
        if not text.endswith("\n"):
            stream.write("\n")
        stream.flush()


def _finish_push(result: PushResult, plan: PushPlan | None) -> int:
    _emit_push_output(result)
    if result.error_message:
        print(result.error_message, file=sys.stderr)
    if result.reviewers is not None and plan is not None:
        for issue in result.reviewers.issues:
            print(f"{issue.level}: {issue.message}", file=sys.stderr)
        if result.reviewers.outcomes:
            print_assignment_report(plan, result.reviewers.outcomes)
    if result.warning:
        print(result.warning, file=sys.stderr)
    return result.returncode


def _print_refusal(
    cwd: Path,
    branch: str,
    refusal: PushRefusal,
    *,
    first_parent: bool,
    settings: Settings,
) -> None:
    for warning in refusal.warnings:
        print(warning, file=sys.stderr)
    if refusal.boundary is not None:
        highlighter = _summary_highlighter_for_push(cwd, branch=branch, first_parent=first_parent, settings=settings)
        print_ready_boundary_notice(refusal.boundary, summary_highlighter=highlighter)
    print(refusal.message, file=sys.stderr)


def _refuses_non_interactive(args: argparse.Namespace) -> bool:
    if stdin_is_terminal() or args.yes:
        return False
    print("error: non-interactive stdin: use --yes (-y) to push without a confirmation prompt", file=sys.stderr)
    return True


def _resolve_push_branch(cwd: Path, branch_arg: str | None, *, settings: Settings) -> str:
    """Return the local branch ``ger push`` operates on."""
    if branch_arg:
        try:
            git_out("rev-parse", "--verify", branch_arg, cwd=cwd)
        except GitError as exc:
            raise GitError(f"branch {branch_arg!r} not found") from exc
        return branch_arg
    branch = resolve_working_branch(cwd, settings=settings)
    if branch is None:
        raise GitError(
            "ger push requires a branch (detached HEAD with no local branch at this commit). "
            "Check out a branch first, or pass --branch."
        )
    return branch


def _session_strategy(args: argparse.Namespace) -> ReviewerStrategy | None:
    if args.reviewer_strategy:
        return ReviewerStrategy(args.reviewer_strategy)
    return None


def _apply_interactive_line(
    cwd: Path,
    plan: PushPlan,
    args: argparse.Namespace,
    *,
    settings: Settings,
    save: bool,
) -> PushPlan | None:
    """Revise *plan* from a push-options line. ``None`` means the line was not applied."""
    res = _prompt_reviewers_line_ptk(
        cwd,
        plan.branch,
        settings=settings,
        change_id_hint=_change_id_for_rev(cwd, plan.tip_sha),
        reviewer_flag_segments=list(args.reviewers),
        session_strategy=_session_strategy(args),
    )
    if not res.valid_for_apply:
        print("Invalid push options; nothing changed.", file=sys.stderr)
        return None
    if not _line_sets_options(res.state):
        print("No push options entered; nothing changed.")
        return None
    revised = revise(plan, _options_from_line(res.state), settings=settings)
    if save and _prompt_save_reviewers():
        set_branch_config(cwd, plan.branch, gerrit_reviewers=",".join(revised.options.reviewers))
    return revised


def _maybe_prompt_initial_options(
    cwd: Path,
    plan: PushPlan,
    args: argparse.Namespace,
    *,
    settings: Settings,
) -> PushPlan:
    res = _prompt_reviewers_line_ptk(
        cwd,
        plan.branch,
        settings=settings,
        change_id_hint=_change_id_for_rev(cwd, plan.branch),
        reviewer_flag_segments=list(args.reviewers),
        session_strategy=_session_strategy(args),
    )
    reviewers = list(plan.options.reviewers)
    if _line_sets_options(res.state):
        plan = revise(plan, _options_from_line(res.state), settings=settings)
        reviewers = list(plan.options.reviewers)
    if _prompt_save_reviewers():
        set_branch_config(cwd, plan.branch, gerrit_reviewers=",".join(reviewers))
    return plan


def _handle_vanilla_push(cwd: Path, args: argparse.Namespace, *, branch: str, settings: Settings) -> int:
    """Handle the vanilla (non-Gerrit) ``git push`` flow and return an exit code."""
    if _gerrit_flags_set(args):
        print(
            "warning: --until, --all, --reviewers, --topic, --wip, --private, "
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
    if _refuses_non_interactive(args):
        return 1
    if not args.yes:
        print(color_text(" ".join(cmd), ANSI_DIM_GRAY))
        print()
        if not confirm("Do you want to run `git push`?", default=True):
            print("Push cancelled.", file=sys.stderr)
            return 1
    logger.debug("gpush vanilla executing: %s (cwd=%s)", " ".join(cmd), cwd)
    return _finish_push(execute(cwd, cmd, settings=settings), None)


def _print_dry_run(plan: PushPlan) -> int:
    print()
    print_plan_status(plan)
    print(color_text(" ".join(push_argv(plan)), ANSI_DIM_GRAY))
    if needs_rest_assignment(plan.options.strategy, plan.options.reviewers):
        label = strategy_status_label(plan.options.strategy)
        print(
            f"[dry-run] after a successful push would apply reviewers via {plan.options.strategy.value} ({label})",
            file=sys.stderr,
        )
    print("[dry-run] not executing push", file=sys.stderr)
    return 0


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
            print_gpush_preview(plan, commit_lines(plan, summary_highlighter), summary_highlighter=summary_highlighter)
            stack_printed = True
        if args.dry_run:
            return _print_dry_run(plan)
        if _refuses_non_interactive(args):
            return 1
        if args.yes:
            if plan.credential_error:
                print(plan.credential_error, file=sys.stderr)
                return 1
            return plan
        print()
        print_plan_status(plan)
        print()
        act = _prompt_gerrit_push_confirm_action()
        if act == "cancel":
            print("Push cancelled.", file=sys.stderr)
            return 0
        if act == "reviewers":
            revised = _apply_interactive_line(cwd, plan, args, settings=settings, save=False)
            if revised is not None:
                plan = revised
            continue
        if plan.credential_error:
            print(plan.credential_error, file=sys.stderr)
            continue
        return plan


def _remember_push_options(cwd: Path, plan: PushPlan, *, settings: Settings) -> None:
    """Save options the user typed, now that a push has shown they work."""
    from gerrit_workflow_tools.push_input_prompt import prepend_push_options_history

    options = plan.options
    state = PushLineState(list(options.reviewers), options.topic, options.wip, options.private)
    web_base, project = _push_options_history_identity(cwd, settings=settings)
    prepend_push_options_history(format_canonical(state, include_strategy=False), web_base=web_base, project=project)


def _push_gerrit(
    cwd: Path,
    args: argparse.Namespace,
    *,
    branch: str,
    settings: Settings,
    gerrit: GerritRest | None,
) -> int:
    defaults = settings.push_defaults
    first_parent = not args.follow_merges
    prepared = prepare(
        cwd,
        branch,
        settings=settings,
        gerrit=gerrit,
        all_commits=bool(args.all_),
        until=args.until,
        first_parent=first_parent,
        reviewer_flags=tuple(args.reviewers),
        strategy=args.reviewer_strategy,
        topic=args.topic,
        wip=bool(args.wip),
        private=bool(args.private),
        no_rebase_check=bool(args.no_rebase_check),
        show_attributes=defaults["show_attributes"],
        update_last_pushed=defaults["last_pushed_branch"],
    )
    if isinstance(prepared, PushRefusal):
        _print_refusal(cwd, branch, prepared, first_parent=first_parent, settings=settings)
        return 1
    for warning in prepared.warnings:
        print(warning, file=sys.stderr)
    plan = prepared
    if args.i:
        plan = _maybe_prompt_initial_options(cwd, plan, args, settings=settings)
    highlighter = _summary_highlighter_for_push(cwd, branch=plan.branch, first_parent=first_parent, settings=settings)
    confirmed = _confirm_gerrit_push(cwd, plan, args, settings=settings, summary_highlighter=highlighter)
    if isinstance(confirmed, int):
        return confirmed
    code = _finish_push(execute(cwd, confirmed, settings=settings, gerrit=gerrit), confirmed)
    if code == 0 and confirmed.options != prepared.options:
        _remember_push_options(cwd, confirmed, settings=settings)
    return code


def main(argv: list[str] | None = None, *, gerrit: GerritRest | None = None) -> int:
    """CLI entry for ``ger push``: compute ready range and push to Gerrit."""
    return run_cli_command(lambda: _run(argv, gerrit=gerrit))


def _run(argv: list[str] | None, *, gerrit: GerritRest | None) -> int:
    args = _build_arg_parser().parse_args(argv)
    cwd, settings, _summary_highlighter = init_cli_runtime(args)
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
        print("error: -i cannot be used with --yes (-y)", file=sys.stderr)
        return 1
    if args.i and not stdin_is_terminal():
        print("error: -i requires an interactive terminal (stdin is not a TTY)", file=sys.stderr)
        return 1

    detached = is_detached_head(cwd)
    branch = _resolve_push_branch(cwd, args.branch, settings=settings)
    mode = ger_push_mode(cwd, branch, settings=settings)
    if not args.yes and not args.dry_run and mode in ("gerrit", None) and (args.branch or not detached):
        if not require_branch_upstream(cwd, branch, settings=settings):
            return 1
        mode = ger_push_mode(cwd, branch, settings=settings)
    if mode is None:
        raise GitError(
            "No push destination: set upstream to your Gerrit remote (`gerrit.remote`, often `origin`; "
            "run `git fetch` first if the tracking branch is missing)."
        )
    if args.i and mode == "vanilla":
        print("error: -i applies only to Gerrit push (upstream on gerrit.remote)", file=sys.stderr)
        return 1
    if mode == "vanilla":
        return _handle_vanilla_push(cwd, args, branch=branch, settings=settings)
    return _push_gerrit(cwd, args, branch=branch, settings=settings, gerrit=gerrit)


if __name__ == "__main__":
    raise SystemExit(main())
