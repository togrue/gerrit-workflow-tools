"""What ``ger push`` reports once ``git push`` ran: errors, reviewer assignments, what was set over REST."""

from __future__ import annotations

import sys

from gerrit_workflow_tools.core.push import PushPlan, PushResult
from gerrit_workflow_tools.core.push_reviewers import ReviewerApplyChangeOutcome
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
    for failure in result.update_failures:
        print(f"warning: could not set {update_line(failure.update)}: {failure.error}", file=sys.stderr)
    if result.warning:
        print(result.warning, file=sys.stderr)
