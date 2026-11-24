"""What ``ger review --vote`` lists, asks and sends: your drafts and the votes, per change.

Spec: docu/spec/commands/review.md § Voting and publishing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from gerrit_workflow_tools.cli_common import ExitCode, confirm_publish
from gerrit_workflow_tools.core.gerrit.change_view import ChangeView
from gerrit_workflow_tools.core.gerrit.comments import PUBLISH_GUARD_ENV, publish_blocked
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.gerrit_patchset import resolve_change
from gerrit_workflow_tools.core.review_checkout import ReviewState, ReviewUsageError, find_review
from gerrit_workflow_tools.core.review_target import VoteTarget, changes_by_number, related_chain_numbers, vote_targets
from gerrit_workflow_tools.core.usage import UsageError
from gerrit_workflow_tools.core.votes import format_votes, parse_votes
from gerrit_workflow_tools.render.comments import format_pending_drafts


def _changes(
    args: argparse.Namespace, service: GerritService, cwd: Path
) -> tuple[list[ChangeView], ReviewState | None]:
    """The changes to vote on, as Gerrit has them now, and the review whose patchset the vote must match.

    ``--change`` (each one named), ``--chain`` (every open change of the chain under review), else
    the change under review.
    """
    if args.change:
        return [resolve_change(service, cwd, changeish).view for changeish in args.change], None
    state = find_review(cwd, service.settings)
    if state is None:
        raise ReviewUsageError("no review going on here: name the change with --change")
    numbers = (related_chain_numbers(service, state.key, state.patch_set) or [state.number]) if args.chain else []
    return changes_by_number(service, numbers or [state.number]), state


def _stale(changes: list[ChangeView], state: ReviewState | None) -> str | None:
    """Why the vote would miss what you read: Gerrit's patchset of the change under review is newer."""
    current = next((c.current_revision_number for c in changes if state and c.number == state.number), None)
    if state is None or current is None or current == state.patch_set:
        return None
    number = state.number
    return f"change {number} is at patchset {current}, you reviewed {state.patch_set}: run ger review {number} first"


def _heading(target: VoteTarget, voted: str) -> str:
    return f'change {target.number} "{target.change.subject}": {target.draft_count} draft(s), {voted}'


def _print_plan(takes: list[VoteTarget], skipped: list[VoteTarget], voted: str, message: str | None) -> None:
    for target in takes:
        print("\n".join(format_pending_drafts(_heading(target, voted), list(target.drafts))))
    if message:
        lines = message.splitlines()
        label = f"  message (on each of {len(takes)} changes): " if len(takes) > 1 else "  message: "
        print("\n".join([label + lines[0], *(" " * len(label) + line for line in lines[1:])]))
    for target in skipped:
        print(f'skipped change {target.number} "{target.change.subject}": {", ".join(target.ruled_out)}')


def _row(target: VoteTarget, votes: dict[str, int], message: str | None, status: str) -> dict[str, object]:
    return {
        "change": target.change.key,
        "drafts": target.draft_count,
        "labels": votes,
        "message": message,
        "status": status,
    }


def vote(args: argparse.Namespace, p: argparse.ArgumentParser, service: GerritService, cwd: Path) -> int:
    """List your drafts and the votes per change, confirm, and publish them: one REST call per change."""
    votes = parse_votes(args.vote)
    if publish_blocked() and not args.dry_run:
        p.error(f"publishing is blocked: {PUBLISH_GUARD_ENV} is set in the environment")
    changes, state = _changes(args, service, cwd)
    if stale := _stale(changes, state):
        print(stale, file=sys.stderr)
        return int(ExitCode.REFUSED)
    targets = vote_targets(service, changes, votes)
    takes = [t for t in targets if not t.ruled_out]
    skipped = [t for t in targets if t.ruled_out]
    if not takes:
        raise UsageError("; ".join(f"change {t.number} does not take {', '.join(t.ruled_out)}" for t in skipped))
    voted = format_votes(votes)
    message = "\n\n".join(args.message) if args.message else None
    if not args.json_:
        _print_plan(takes, skipped, voted, message)
    count = sum(t.draft_count for t in takes)
    on = ", ".join(str(t.number) for t in takes)
    rows: list[dict[str, object]] = []
    if not args.dry_run and not confirm_publish(args, f"{count} draft(s) and {voted} on {on}"):
        return int(ExitCode.REFUSED)
    for target in takes:
        if not args.dry_run:
            service.comments.publish_drafts(target.change.key, message=message, labels=votes)
            if not args.json_:
                print(f"published {target.draft_count} draft(s) and {voted} on {target.number}")
        rows.append(_row(target, votes, message, "dry-run" if args.dry_run else "published"))
    if args.json_:
        skips = [{"change": t.change.key, "ruled_out": list(t.ruled_out)} for t in skipped]
        print(json.dumps(rows[0] if len(targets) == 1 else {"changes": rows, "skipped": skips}, indent=2))
    elif state is not None and not args.dry_run:
        print("next: ger review --done" if args.chain else "next: ger review --next · ger review --done")
    return int(ExitCode.OK)
