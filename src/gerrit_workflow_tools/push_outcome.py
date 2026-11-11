"""What ``ger push`` reports once ``git push`` ran: errors, reviewer assignments, what was set over REST."""

from __future__ import annotations

import sys
from collections.abc import Sequence

from gerrit_workflow_tools.cli_style import color_short_sha
from gerrit_workflow_tools.core.push import PushPlan
from gerrit_workflow_tools.core.push_result import PushResult
from gerrit_workflow_tools.core.push_reviewers import ReviewerApplyChangeOutcome
from gerrit_workflow_tools.core.push_unchanged import StateUpdateFailure
from gerrit_workflow_tools.push_preview import update_line


def _escape_topic_for_assignment_report(topic: str) -> str:
    return topic.replace("\\", "\\\\").replace('"', '\\"')


def _assignment_attributes_fragment(plan: PushPlan, reviewers_assigned: tuple[str, ...]) -> str:
    parts: list[str] = []
    if plan.options.topic:
        parts.append(f'topic="{_escape_topic_for_assignment_report(plan.options.topic)}"')
    parts.extend(f"r={name}" for name in reviewers_assigned)
    if plan.options.wip:
        parts.append("wip")
    if plan.options.ready:
        parts.append("ready")
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


def _failure_lines(failures: Sequence[StateUpdateFailure]) -> list[str]:
    """One line per distinct failure: the same refusal on several commits is said once, naming them."""
    groups: dict[tuple[str, str], list[StateUpdateFailure]] = {}
    for failure in failures:
        groups.setdefault((failure.update.describe(), failure.error), []).append(failure)
    lines = []
    for (what, error), group in groups.items():
        if len(group) == 1:
            lines.append(f"{update_line(group[0].update)}: {error}")
            continue
        shas = ", ".join(color_short_sha(failure.update.change.commit.short_sha) for failure in group)
        lines.append(f"{what} on {len(group)} commits ({shas}): {error}")
    return lines


def print_push_outcome(result: PushResult, plan: PushPlan | None) -> None:
    """What happened after ``git push``: errors, reviewer assignments, warnings."""
    if result.error_message:
        print(result.error_message, file=sys.stderr)
    if result.reviewers is not None and plan is not None:
        for issue in result.reviewers.issues:
            print(f"{issue.level}: {issue.message}", file=sys.stderr)
        if result.reviewers.outcomes:
            print_assignment_report(plan, result.reviewers.outcomes)
    for update in result.updated:
        print(f"set {update_line(update)}")
    for line in _failure_lines(result.update_failures):
        print(f"warning: could not set {line}", file=sys.stderr)
    if result.warning:
        print(result.warning, file=sys.stderr)
