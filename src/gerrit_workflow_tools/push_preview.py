"""What ``ger push`` shows and asks: commit list, boundary notice, status line, confirmation."""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable
from pathlib import Path

from gerrit_workflow_tools.cli_common import ask_choice, confirm
from gerrit_workflow_tools.cli_terminal import prompt_toolkit_can_run
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.config_write import set_branch_config
from gerrit_workflow_tools.core.push import (
    PushPlan,
    change_ref_of,
    publish_drafts_with,
    push_argvs,
    revise,
    sent_commits,
    state_updates,
    unsent_short_shas,
    wip_commits,
)
from gerrit_workflow_tools.core.push_checks import PushBoundary
from gerrit_workflow_tools.core.push_options import PushOptions
from gerrit_workflow_tools.core.push_result import NOTHING_TO_PUSH
from gerrit_workflow_tools.core.push_reviewers import PushMark, needs_rest_assignment
from gerrit_workflow_tools.core.push_unchanged import StateUpdate
from gerrit_workflow_tools.core.reviewer import ReviewerStrategy
from gerrit_workflow_tools.push_input_line import KW_PUBLISH_DRAFTS, ParseResult, PushLineState
from gerrit_workflow_tools.render.style import (
    ANSI_BLUE,
    ANSI_BOLD,
    ANSI_CYAN,
    ANSI_DIM,
    ANSI_DIM_GRAY,
    ANSI_GREEN,
    ANSI_LIGHT_GREEN,
    ANSI_YELLOW,
    color_short_sha,
    color_text,
)
from gerrit_workflow_tools.summary_highlight import SummaryHighlighter


def strategy_status_label(strategy: ReviewerStrategy) -> str:
    return {
        ReviewerStrategy.PUSH: "push (new changes are modified)",
        ReviewerStrategy.LAZY: "lazy (non-assigned are modified)",
        ReviewerStrategy.OVERWRITE: "overwrite (all changes are modified)",
    }[strategy]


def _format_attribute_string(reviewers: tuple[str, ...] | list[str], wip: bool, private: bool) -> str:
    parts: list[str] = [f"r={name}" for name in reviewers]
    if wip:
        parts.append("wip")
    if private:
        parts.append("private")
    return ",".join(parts) if parts else "(none)"


def _attribute_suffix(mark: PushMark, reviewers: tuple[str, ...]) -> str:
    """Append `` - `…` `` or `` - `…` -> `…` `` for attribute preview lines."""
    if not mark.has_remote:
        current = "(none)"
        new = _format_attribute_string(reviewers, False, False)
    else:
        current = _format_attribute_string(mark.reviewers, mark.wip, mark.private)
        new = _format_attribute_string(reviewers, mark.wip, mark.private)
    if current == new:
        return f" - `{current}`"
    return f" - `{current}` -> `{new}`"


def _stop_pattern_from_reason(boundary_reason: str) -> str | None:
    match = re.search(r"stop pattern (.+)$", boundary_reason)
    return match.group(1).strip() if match else None


def _boundary_notice(boundary_line: str, because: str, detail: str) -> str:
    """Explain a ready boundary without wrapping highlighted commit text."""
    quote = color_text('"', ANSI_YELLOW)
    return (
        f"Stopped at commit {quote}{boundary_line}{quote}{because}"
        f"{color_text(detail, ANSI_DIM_GRAY)}{color_text('.', ANSI_YELLOW)}"
    )


def _format_boundary_commit_line(boundary: PushBoundary, *, summary_highlighter: SummaryHighlighter) -> str | None:
    if not boundary.sha or not boundary.short_sha or boundary.subject is None:
        return None
    sha_p = boundary.short_sha.ljust(8)
    highlighted = summary_highlighter.highlight(boundary.subject, sha=boundary.sha)
    return f"{color_short_sha(sha_p)}{color_text(' # ', ANSI_DIM)}{highlighted}"


def print_ready_boundary_notice(boundary: PushBoundary, *, summary_highlighter: SummaryHighlighter) -> None:
    """Print why the ready boundary stopped the stack (stop pattern or Change-Id, etc.)."""
    boundary_line = _format_boundary_commit_line(boundary, summary_highlighter=summary_highlighter)
    if not boundary_line:
        return
    print()
    pat = _stop_pattern_from_reason(boundary.reason)
    if pat:
        print(_boundary_notice(boundary_line, ", because it matches the stop pattern ", pat))
    else:
        print(_boundary_notice(boundary_line, color_text(", because ", ANSI_YELLOW), boundary.reason))
    if boundary.remaining_unpushed > 0:
        print(color_text(f"... {boundary.remaining_unpushed} not-ready commit(s) remain unpushed", ANSI_YELLOW))


def _commit_lines(plan: PushPlan, summary_highlighter: SummaryHighlighter) -> list[str]:
    lines: list[str] = []
    wip = {c.sha for c in wip_commits(plan)} if plan.wip_from else set()
    for commit in sent_commits(plan):
        disp = summary_highlighter.highlight(commit.subject, sha=commit.sha)
        sha_p = commit.short_sha.ljust(8)
        line = f"    {color_short_sha(sha_p)}{color_text(' # ', ANSI_DIM)}{disp}"
        if plan.marks is not None and commit.change_id:
            mark = plan.marks.get(commit.change_id, PushMark(False, (), False, False))
            line += _attribute_suffix(mark, plan.options.reviewers)
        if commit.sha in wip:
            line += color_text("  [WIP]", ANSI_YELLOW)
        lines.append(line)
    return lines


def _commits(count: int) -> str:
    return "1 commit Gerrit already has is" if count == 1 else f"{count} commits Gerrit already has are"


def print_gpush_preview(plan: PushPlan, *, summary_highlighter: SummaryHighlighter) -> None:
    """What the push sends and what it sets over REST, each listed once, under a heading only when it has any.

    Commits Gerrit already has with nothing to set are one line, not a list.
    """
    heading = f"{ANSI_BOLD}{ANSI_CYAN}"
    sent, updates = sent_commits(plan), state_updates(plan)
    if sent:
        print(color_text("About to push commits:", heading))
        for line in _commit_lines(plan, summary_highlighter):
            print(line)
    if updates:
        rest = "Gerrit already has these at that commit; set over REST instead:"
        print(color_text(rest, ANSI_DIM) if sent else color_text("No new commits to push; set on Gerrit:", heading))
        for update in updates:
            print(f"    {update_line(update, summary_highlighter)}")
    if sent and (idle := len(plan.commits) - len(sent) - len(updates) - len(plan.outdated)):
        print(color_text(f"{_commits(idle)} not sent again", ANSI_DIM))
    if not sent and not updates:
        print(NOTHING_TO_PUSH)
    print_outdated(plan)
    print_ready_boundary_notice(plan.boundary, summary_highlighter=summary_highlighter)


def print_outdated(plan: PushPlan) -> None:
    """One dim line per outdated commit: Gerrit keeps its newer patchset, and how to take it."""
    for change in plan.outdated:
        print(color_text(change.describe(), ANSI_DIM))


def update_line(update: StateUpdate, summary_highlighter: SummaryHighlighter | None = None) -> str:
    """``<sha> # <subject>  <what is set>``: one change Gerrit already has, and what a push sets on it."""
    commit = update.change.commit
    subject = summary_highlighter.highlight(commit.subject, sha=commit.sha) if summary_highlighter else commit.subject
    return f"{color_short_sha(commit.short_sha.ljust(8))}{color_text(' # ', ANSI_DIM)}{subject}  {update.describe()}"


def _wip_field(plan: PushPlan, yes_no: Callable[[bool], str]) -> str:
    if plan.wip_from:
        wip = wip_commits(plan)
        return color_text(f"from {wip[0].short_sha} ({len(wip)} of {len(plan.commits)})", ANSI_YELLOW)
    return color_text("no (--ready)", ANSI_GREEN) if plan.options.ready else yes_no(plan.options.wip)


def print_plan_status(plan: PushPlan) -> None:
    """One-line summary before the push confirmation prompt."""
    options = plan.options
    none = color_text("(none)", ANSI_DIM)

    def _yes_no(on: bool) -> str:
        return color_text("yes", ANSI_GREEN) if on else color_text("no", ANSI_DIM)

    fields = [
        ("Branch", color_text(plan.branch, f"{ANSI_BOLD}{ANSI_CYAN}")),
        ("Target", color_text(f"refs/for/{plan.push_branch}", ANSI_GREEN)),
        ("Reviewers", color_text(", ".join(options.reviewers), ANSI_LIGHT_GREEN) if options.reviewers else none),
        ("Topic", color_text(options.topic, ANSI_CYAN) if options.topic else none),
        ("WIP", _wip_field(plan, _yes_no)),
        (
            "Private",
            color_text("no (--no-private)", ANSI_GREEN) if options.remove_private else _yes_no(options.private),
        ),
    ]
    if options.strategy != ReviewerStrategy.PUSH:
        fields.append(("Strategy", color_text(strategy_status_label(options.strategy), ANSI_DIM)))
    unsent = unsent_short_shas(plan)
    stuck = [sha for sha in plan.drafts if sha in unsent]
    updated = sum(count for sha, count in plan.drafts.items() if sha not in stuck)
    if updated:
        decision = "publish" if options.publish_comments else "keep"
        fields.append(
            ("Drafts", color_text(f"{decision} {updated}", ANSI_BLUE if options.publish_comments else ANSI_DIM))
        )
    print(color_text("  |  ", ANSI_DIM).join(f"{color_text(label, ANSI_DIM)} {value}" for label, value in fields))
    if stuck:
        print(color_text(f"drafts on {', '.join(stuck)} stay drafts: Gerrit gets no new patchset there", ANSI_DIM))


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


def print_dry_run(plan: PushPlan) -> None:
    """Status line and the ``git push`` that would run; what would happen after it on stderr."""
    print()
    print_plan_status(plan)
    if not sent_commits(plan):  # what the real run does: no git push, REST only
        print("[dry-run] no git push: Gerrit already has these commits", file=sys.stderr)
    else:
        for argv in push_argvs(plan):
            print(color_text(" ".join(argv), ANSI_DIM_GRAY))
    if needs_rest_assignment(plan.options.strategy, plan.options.reviewers):
        label = strategy_status_label(plan.options.strategy)
        print(
            f"[dry-run] after a successful push would apply reviewers via {plan.options.strategy.value} ({label})",
            file=sys.stderr,
        )
    print("[dry-run] not executing push", file=sys.stderr)


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
) -> ParseResult:
    """Open the highlighted push-options input line, prefilled from flags/registry unless *prefill* is off."""
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
        change_ref=change_ref_of(cwd, plan, plan.tip_sha, settings=settings),
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
    if save and prompt_save_reviewers():
        set_branch_config(cwd, plan.branch, gerrit_reviewers=",".join(revised.options.reviewers))
    return revised


def options_line_available() -> bool:
    """True when the confirmation can be the options input line (it needs a terminal on both ends)."""
    return prompt_toolkit_can_run() and sys.stdout.isatty()


_CONFIRM_LINE_MESSAGE = "Push options (Enter {}, Up: history, Ctrl-C cancels): "


def confirm_with_options_line(
    cwd: Path, plan: PushPlan, args: argparse.Namespace, *, settings: Settings, drafts: int = 0
) -> PushPlan | None:
    """The one-line push confirmation: Enter pushes, a typed line sets options first. ``None`` cancels.

    An empty line keeps the options *plan* already has, as the status line shows them. When the
    push sends no commit, Enter sets them on Gerrit, and the line says that instead. With *drafts*, the
    line starts with ``publish-drafts`` when *plan* publishes them, and the keyword decides whether it does.
    """
    try:
        res = prompt_reviewers_line(
            cwd,
            plan.branch,
            settings=settings,
            change_ref=change_ref_of(cwd, plan, plan.tip_sha, settings=settings),
            reviewer_flag_segments=list(args.reviewers),
            session_strategy=_session_strategy(args),
            message=_CONFIRM_LINE_MESSAGE.format("pushes" if sent_commits(plan) else "sets them on Gerrit"),
            prefill=False,
            suffix=KW_PUBLISH_DRAFTS if drafts and plan.options.publish_comments else "",
        )
    except (EOFError, KeyboardInterrupt):
        return None
    if _line_sets_options(res.state):
        plan = revise(plan, _options_from_line(res.state), settings=settings)
    return publish_drafts_with(plan, res.state.publish_drafts) if drafts else plan


def prompt_initial_options(
    cwd: Path,
    plan: PushPlan,
    args: argparse.Namespace,
    *,
    settings: Settings,
) -> PushPlan:
    res = prompt_reviewers_line(
        cwd,
        plan.branch,
        settings=settings,
        change_ref=change_ref_of(cwd, plan, plan.branch, settings=settings),
        reviewer_flag_segments=list(args.reviewers),
        session_strategy=_session_strategy(args),
    )
    if _line_sets_options(res.state):
        plan = revise(plan, _options_from_line(res.state), settings=settings)
    if prompt_save_reviewers():
        set_branch_config(cwd, plan.branch, gerrit_reviewers=",".join(plan.options.reviewers))
    return plan
