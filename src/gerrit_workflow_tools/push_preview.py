"""What ``ger push`` shows: commit list, boundary notice, status line, and what it assigned."""

from __future__ import annotations

import re

from gerrit_workflow_tools.cli_style import (
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
from gerrit_workflow_tools.core.push import PushBoundary, PushPlan
from gerrit_workflow_tools.core.push_reviewers import PushMark, ReviewerApplyChangeOutcome
from gerrit_workflow_tools.core.reviewer import ReviewerStrategy
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
    if not match:
        return None
    return match.group(1).strip()


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


def commit_lines(plan: PushPlan, summary_highlighter: SummaryHighlighter) -> list[str]:
    lines: list[str] = []
    for commit in plan.commits:
        disp = summary_highlighter.highlight(commit.subject, sha=commit.sha)
        sha_p = commit.short_sha.ljust(8)
        line = f"    {color_short_sha(sha_p)}{color_text(' # ', ANSI_DIM)}{disp}"
        if plan.marks is not None and commit.change_id:
            mark = plan.marks.get(commit.change_id, PushMark(False, (), False, False))
            line += _attribute_suffix(mark, plan.options.reviewers)
        lines.append(line)
    return lines


def print_gpush_preview(plan: PushPlan, commit_lines: list[str], *, summary_highlighter: SummaryHighlighter) -> None:
    print(color_text("About to push commits:", f"{ANSI_BOLD}{ANSI_CYAN}"))
    for line in commit_lines:
        print(line)
    print_ready_boundary_notice(plan.boundary, summary_highlighter=summary_highlighter)


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
        ("WIP", _yes_no(options.wip)),
        ("Private", _yes_no(options.private)),
    ]
    if options.strategy != ReviewerStrategy.PUSH:
        fields.append(("Strategy", color_text(strategy_status_label(options.strategy), ANSI_DIM)))
    print(color_text("  |  ", ANSI_DIM).join(f"{color_text(label, ANSI_DIM)} {value}" for label, value in fields))


def _escape_topic_for_assignment_report(topic: str) -> str:
    return topic.replace("\\", "\\\\").replace('"', '\\"')


def _assignment_attributes_fragment(plan: PushPlan, reviewers_assigned: tuple[str, ...]) -> str:
    parts: list[str] = []
    if plan.options.topic:
        parts.append(f'topic="{_escape_topic_for_assignment_report(plan.options.topic)}"')
    parts.extend(f"r={name}" for name in reviewers_assigned)
    if plan.options.wip:
        parts.append("wip")
    if plan.options.private:
        parts.append("private")
    return ",".join(parts)


def print_assignment_report(plan: PushPlan, outcomes: list[ReviewerApplyChangeOutcome]) -> None:
    by_cid = {commit.change_id: (commit.sha, commit.subject) for commit in plan.commits if commit.change_id}
    for outcome in outcomes:
        row = by_cid.get(outcome.change_id)
        if row is None:
            continue
        sha, subject = row
        frag = _assignment_attributes_fragment(plan, outcome.reviewers_assigned)
        if not frag:
            continue
        print(f"{sha} {subject} assigned {frag}")
