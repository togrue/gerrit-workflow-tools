"""What ``ger review --vote`` lists, asks and sends: your drafts and the votes, per change.

Spec: docu/spec/commands/review.md § Voting and publishing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from gerrit_workflow_tools.cli_common import ExitCode, confirm_publish
from gerrit_workflow_tools.core.gerrit.comments import PUBLISH_GUARD_ENV, publish_blocked
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.review_target import VoteTarget, plan_vote, review_moved, send_vote, vote_changes
from gerrit_workflow_tools.core.votes import format_votes, parse_votes
from gerrit_workflow_tools.render.comments import format_pending_drafts


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
    changes, state = vote_changes(service, cwd, args.change or [], chain=args.chain)
    if stale := review_moved(changes, state):
        print(stale, file=sys.stderr)
        return int(ExitCode.REFUSED)
    message = "\n\n".join(args.message) if args.message else None
    plan = plan_vote(service, changes, votes, message)
    takes, skipped = plan.takes, plan.skipped
    voted = format_votes(votes)
    if not args.json_:
        _print_plan(takes, skipped, voted, message)
    on = ", ".join(str(t.number) for t in takes)
    if not args.dry_run and not confirm_publish(args, f"{plan.draft_count} draft(s) and {voted} on {on}"):
        return int(ExitCode.REFUSED)

    def sent(target: VoteTarget) -> None:
        if not args.json_:
            print(f"published {target.draft_count} draft(s) and {voted} on {target.number}")

    if not args.dry_run:
        send_vote(service, plan, sent=sent)
    rows = [_row(t, votes, message, "dry-run" if args.dry_run else "published") for t in takes]
    if args.json_:
        skips = [{"change": t.change.key, "ruled_out": list(t.ruled_out)} for t in skipped]
        print(json.dumps(rows[0] if len(plan.targets) == 1 else {"changes": rows, "skipped": skips}, indent=2))
    elif state is not None and not args.dry_run:
        print("next: ger review --done" if args.chain else "next: ger review --next · ger review --done")
    return int(ExitCode.OK)
