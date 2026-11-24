"""What ``ger push`` shows and asks: commit list, boundary notice, status line, confirmation."""

from __future__ import annotations

import re
import sys
from collections.abc import Callable

from gerrit_workflow_tools.core.push import (
    PushPlan,
    push_argvs,
    sent_commits,
    state_updates,
    unsent_short_shas,
    wip_commits,
)
from gerrit_workflow_tools.core.push_checks import PushBoundary
from gerrit_workflow_tools.core.push_result import NOTHING_TO_PUSH
from gerrit_workflow_tools.core.push_reviewers import PushMark, needs_rest_assignment
from gerrit_workflow_tools.core.push_unchanged import StateUpdate
from gerrit_workflow_tools.core.reviewer import ReviewerStrategy
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


def print_ready_boundary_notice(
    boundary: PushBoundary, *, summary_highlighter: SummaryHighlighter, gap: bool = True
) -> None:
    """Why the ready boundary stopped the stack (stop pattern or Change-Id, etc.), and what gets past it."""
    boundary_line = _format_boundary_commit_line(boundary, summary_highlighter=summary_highlighter)
    if not boundary_line:
        return
    if gap:
        print()
    pat = _stop_pattern_from_reason(boundary.reason)
    if pat:
        print(_boundary_notice(boundary_line, ", because it matches the stop pattern ", pat))
    else:
        print(_boundary_notice(boundary_line, color_text(", because ", ANSI_YELLOW), boundary.reason))
    if boundary.held_back > 0:
        held = "1 commit" if boundary.held_back == 1 else f"{boundary.held_back} commits from {boundary.short_sha} up"
        fix = f"; {boundary.fix}" if boundary.fix else ""
        print(color_text(f"... {held} held back by the ready boundary{fix}", ANSI_YELLOW))


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
    if not sent and not updates:
        print_nothing_to_push(plan, summary_highlighter=summary_highlighter)
        return
    if sent:
        print(color_text("About to push commits:", heading))
        for line in _commit_lines(plan, summary_highlighter):
            print(line)
    if updates:
        rest = "Gerrit already has these at that commit; set over REST instead:"
        print(color_text(rest, ANSI_DIM) if sent else color_text("No new commits to push; set on Gerrit:", heading))
        for update in updates:
            print(f"    {update_line(update, summary_highlighter)}")
    if sent and (idle := len(plan.commits) - len(sent) - len(updates) - len(plan.outdated) - len(plan.abandoned)):
        print(color_text(f"{_commits(idle)} not sent again", ANSI_DIM))
    print_not_sent(plan)
    print_ready_boundary_notice(plan.boundary, summary_highlighter=summary_highlighter)


def print_nothing_to_push(plan: PushPlan, *, summary_highlighter: SummaryHighlighter) -> None:
    """A push that changes nothing on Gerrit: where the ready boundary stopped it, then that Gerrit has the rest.

    The same lines whether it is a dry run or not (``changes_nothing``).
    """
    print_ready_boundary_notice(plan.boundary, summary_highlighter=summary_highlighter, gap=False)
    print(NOTHING_TO_PUSH)
    print_not_sent(plan)


def print_not_sent(plan: PushPlan) -> None:
    """One line per commit Gerrit has and the push does not send: outdated (dim), or on an abandoned change."""
    for change in plan.outdated:
        print(color_text(change.describe(), ANSI_DIM))
    for abandoned in plan.abandoned:
        print(color_text(abandoned.describe(), ANSI_YELLOW))


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


def print_dry_run(plan: PushPlan) -> None:
    """Status line and the ``git push`` that would run; what would happen after it on stderr."""
    print()
    print_plan_status(plan)
    sys.stdout.flush()  # what would run comes after what was shown, also when both go to one pipe
    if not sent_commits(plan):  # what the real run does: no git push, REST only
        if state_updates(plan):
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
