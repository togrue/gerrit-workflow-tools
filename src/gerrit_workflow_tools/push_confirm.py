"""What ``ger push`` asks: the confirm loop, the push-options line, the Y/n/r question, ``-i``, saving reviewers.

The confirm loop stays in the command, not in ``core/push.py`` (ADR-0008); this module is that part of it.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from gerrit_workflow_tools.cli_common import ask_choice, confirm
from gerrit_workflow_tools.cli_terminal import prompt_toolkit_can_run, stdin_is_terminal
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.config_write import set_branch_config
from gerrit_workflow_tools.core.push import (
    PushPlan,
    publish_drafts_with,
    publishable_drafts,
    push_argv,
    revise,
    sent_commits,
    unknown_plan_reviewers,
)
from gerrit_workflow_tools.core.push_options import PushOptions
from gerrit_workflow_tools.core.push_reviewers import change_ref_of
from gerrit_workflow_tools.core.reviewer import ReviewerStrategy
from gerrit_workflow_tools.core.run_context import RunContext
from gerrit_workflow_tools.core.usage import UsageError
from gerrit_workflow_tools.push_input_line import KW_PUBLISH_DRAFTS, ParseResult, PushLineState, format_canonical
from gerrit_workflow_tools.push_input_prompt import LINE_HELP
from gerrit_workflow_tools.push_preview import print_dry_run, print_gpush_preview, print_plan_status
from gerrit_workflow_tools.summary_highlight import SummaryHighlighter


logger = logging.getLogger(__name__)


def require_confirmable(*, yes: bool) -> None:
    if not (stdin_is_terminal() or yes):
        raise UsageError("non-interactive stdin: use --yes (-y) to push without a confirmation prompt")


def ask_push_action(drafts: int, *, sends: bool = True) -> str:
    """Ask the push confirmation: ``push``, ``cancel`` or ``reviewers``, plus ``push-only`` with *drafts*.

    With drafts, the question offers them: ``push`` publishes them with the push, ``push-only`` keeps them.
    A push that *sends* no commit only sets options over REST, and the question says so.
    """
    answers = {
        "": "push",
        "y": "push",
        "yes": "push",
        "n": "cancel",
        "no": "cancel",
        "r": "reviewers",
        "reviewer": "reviewers",
        "reviewers": "reviewers",
    }
    if drafts:
        return ask_choice(
            f"Push and publish {drafts} draft(s)? [Y/n/p/r]: ",
            {**answers, "p": "push-only"},
            hint="  (Enter or y: push and publish, p: push and keep the drafts, n: cancel, r: set reviewers)",
            on_eof="cancel",
        )
    question, verb = ("Do you want to push these commits?", "push") if sends else ("Set these on Gerrit?", "set them")
    return ask_choice(
        f"{question} [Y/n/r]: ",
        answers,
        hint=f"  (Enter or y: {verb}, n: cancel, r: set reviewers and strategy)",
        on_eof="cancel",
    )


def prompt_save_reviewers() -> bool:
    return confirm("Save reviewers to branch config?", default=False)


def prompt_reviewers_line(
    cwd: Path | None = None,
    branch: str | None = None,
    *,
    settings: Settings,
    change_ref: str | None = None,
    reviewer_flag_segments: list[str] | None = None,
    session_strategy: ReviewerStrategy | None = None,
    message: str = "Push options: ",
    prefill: bool = True,
    suffix: str = "",
    text: str | None = None,
) -> ParseResult:
    """Open the highlighted push-options input line, prefilled from flags/registry unless *prefill* is off.

    A *text* is the starting line instead, when the line opens again after help or a refused reviewer.
    """
    from gerrit_workflow_tools.push_input_prompt import prompt_branch_push_options

    return prompt_branch_push_options(
        cwd,
        branch,
        settings=settings,
        change_ref=change_ref,
        reviewer_flag_segments=reviewer_flag_segments,
        session_strategy=session_strategy,
        message=message,
        prefill=prefill,
        suffix=suffix,
        text=text,
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


def _session_strategy(args: argparse.Namespace) -> ReviewerStrategy | None:
    return ReviewerStrategy(args.reviewer_strategy) if args.reviewer_strategy else None


def apply_interactive_line(
    cwd: Path,
    plan: PushPlan,
    args: argparse.Namespace,
    *,
    settings: Settings,
    save: bool,
) -> PushPlan | None:
    """Revise *plan* from a push-options line. ``None`` means the line was not applied."""
    res = prompt_reviewers_line(
        cwd,
        plan.branch,
        settings=settings,
        change_ref=change_ref_of(cwd, plan.push_branch, plan.tip_sha, settings=settings),
        reviewer_flag_segments=list(args.reviewers),
        session_strategy=_session_strategy(args),
    )
    if not res.valid_for_apply:
        print("Invalid push options; nothing changed.", file=sys.stderr)
        return None
    if not _line_sets_options(res.state) and res.state.answer is None:
        print("No push options entered; nothing changed.")
        return None
    if res.state.answer is not None:
        print(LINE_HELP if res.state.answer == "help" else "Nothing changed.")
        return None
    revised = revise(plan, _options_from_line(res.state), settings=settings)
    if save and prompt_save_reviewers():
        set_branch_config(cwd, plan.branch, gerrit_reviewers=",".join(revised.options.reviewers))
    return revised


def options_line_available() -> bool:
    """True when the confirmation can be the options input line (it needs a terminal on both ends)."""
    return prompt_toolkit_can_run() and sys.stdout.isatty()


_CONFIRM_LINE_MESSAGE = "Push options (Enter {}, Up: history, Ctrl-C cancels): "


def _no_account(unknown: tuple[str, ...]) -> str:
    """Why a push with reviewers *unknown* does not go up (:func:`unknown_plan_reviewers`)."""
    return f"Gerrit has no account {', '.join(f'`{name}`' for name in unknown)}; nothing was pushed or set"


def confirm_with_options_line(
    run: RunContext, plan: PushPlan, args: argparse.Namespace, *, drafts: int = 0
) -> PushPlan | None:
    """The one-line push confirmation: Enter (or ``y``) pushes, a typed line sets options first. ``None`` cancels.

    An empty line keeps the options *plan* already has, as the status line shows them. When the
    push sends no commit, Enter sets them on Gerrit, and the line says that instead. With *drafts*, the
    line starts with ``publish-drafts`` when *plan* publishes them, and the keyword decides whether it does.
    ``n`` / ``q`` cancel, ``?`` / ``help`` print the grammar and ask again. A reviewer Gerrit has no account
    for asks again too, with the line as typed: a refused line sets nothing.
    """
    text: str | None = None
    while True:
        try:
            res = prompt_reviewers_line(
                run.cwd,
                plan.branch,
                settings=run.settings,
                change_ref=change_ref_of(run.cwd, plan.push_branch, plan.tip_sha, settings=run.settings),
                reviewer_flag_segments=list(args.reviewers),
                session_strategy=_session_strategy(args),
                message=_CONFIRM_LINE_MESSAGE.format("pushes" if sent_commits(plan) else "sets them on Gerrit"),
                prefill=False,
                suffix=KW_PUBLISH_DRAFTS if drafts and plan.options.publish_comments else "",
                text=text,
            )
        except (EOFError, KeyboardInterrupt):
            return None
        state, text = res.state, format_canonical(res.state)
        if state.answer == "cancel":
            return None
        if state.answer == "help":
            print(LINE_HELP)
            continue
        revised = revise(plan, _options_from_line(state), settings=run.settings) if _line_sets_options(state) else plan
        revised = publish_drafts_with(revised, state.publish_drafts) if drafts else revised
        if unknown := unknown_plan_reviewers(run, revised):
            print(f"error: {_no_account(unknown)}. Fix the name, or drop it with -{unknown[0]}", file=sys.stderr)
            continue
        return revised


def prompt_initial_options(
    cwd: Path, plan: PushPlan, args: argparse.Namespace, *, settings: Settings
) -> PushPlan | None:
    """``-i``: the push options line before the preview. ``None``: the line cancelled the push."""
    text: str | None = None
    while True:
        res = prompt_reviewers_line(
            cwd,
            plan.branch,
            settings=settings,
            change_ref=change_ref_of(cwd, plan.push_branch, plan.branch, settings=settings),
            reviewer_flag_segments=list(args.reviewers),
            session_strategy=_session_strategy(args),
            text=text,
        )
        if res.state.answer != "help":
            break
        print(LINE_HELP)
        text = format_canonical(res.state)
    if res.state.answer == "cancel":
        return None
    if _line_sets_options(res.state):
        plan = revise(plan, _options_from_line(res.state), settings=settings)
    if prompt_save_reviewers():
        set_branch_config(cwd, plan.branch, gerrit_reviewers=",".join(plan.options.reviewers))
    return plan


def confirm_gerrit_push(
    run: RunContext, plan: PushPlan, args: argparse.Namespace, *, summary_highlighter: SummaryHighlighter
) -> PushPlan | int:
    """Return the plan to execute, or an exit code when the command should stop.

    Reviewers Gerrit has no account for stop the push before anything is sent: off a terminal (and
    under ``--dry-run``) as a usage error, at the prompt by asking again.
    """
    settings = run.settings
    stack_printed = False
    drafts_defaulted = False
    while True:
        argv = push_argv(plan)
        logger.debug(
            "ger push resolved: remote=%r push_branch=%r reviewers=%s strategy=%s refspec=%r",
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
            if unknown := unknown_plan_reviewers(run, plan):
                raise UsageError(_no_account(unknown))
            return 0
        require_confirmable(yes=args.yes)
        if args.yes:
            if unknown := unknown_plan_reviewers(run, plan):
                raise UsageError(_no_account(unknown))
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
        if line:  # the line asks Gerrit about its reviewers itself
            revised = confirm_with_options_line(run, plan, args, drafts=drafts)
            act = "push" if revised else "cancel"
            plan = revised or plan
        else:
            asked = drafts if plan.options.publish_comments is False else 0
            act = ask_push_action(asked, sends=bool(sent_commits(plan)))
            if act == "push" and asked:
                plan = publish_drafts_with(plan, True)
            if act == "push" and (unknown := unknown_plan_reviewers(run, plan)):
                print(f"error: {_no_account(unknown)}. Answer r to change the reviewers", file=sys.stderr)
                continue
        if act == "cancel":
            print("Push cancelled.", file=sys.stderr)
            return 0
        if act == "reviewers":
            revised = apply_interactive_line(run.cwd, plan, args, settings=settings, save=False)
            if revised is not None:
                plan = revised
            continue
        if plan.credential_error:
            print(plan.credential_error, file=sys.stderr)
            continue
        return plan
