"""What ``ger review --vote`` lists, asks and sends: your drafts and the votes, per change.

Spec: docu/spec/commands/review.md § Voting and publishing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from gerrit_workflow_tools.cli_args import read_message
from gerrit_workflow_tools.cli_common import ExitCode, confirm_publish
from gerrit_workflow_tools.core.gerrit.comments import PUBLISH_GUARD_ENV, publish_blocked
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.review_target import (
    VotePlan,
    VoteTarget,
    plan_vote,
    review_moved,
    send_vote,
    vote_changes,
)
from gerrit_workflow_tools.core.votes import format_votes, parse_votes
from gerrit_workflow_tools.core.words import counted
from gerrit_workflow_tools.render.comments import format_pending_drafts
from gerrit_workflow_tools.render.review_json import sent_vote_json
from gerrit_workflow_tools.render.wrap import say


def _what(target: VoteTarget, plan: VotePlan) -> str:
    """``1 draft, Code-Review+1``; ``2 drafts, keeps your Code-Review-1``: what goes to *target*."""
    parts = [counted(target.draft_count, "draft")]
    if sent := plan.votes_on(target):
        parts.append(format_votes(sent))
    if kept := plan.keeps(target):
        parts.append(f"keeps your {format_votes(kept)}")
    return ", ".join(parts)


def _print_plan(plan: VotePlan) -> None:
    for target in plan.takes:
        heading = f'change {target.number} "{target.change.subject}": {_what(target, plan)}'
        print("\n".join(format_pending_drafts(heading, list(target.drafts))))
    if plan.message:
        lines = plan.message.splitlines()
        on = len(plan.sends)
        label = f"  message (on each of {on} changes): " if on > 1 else "  message: "
        print("\n".join([label + lines[0], *(" " * len(label) + line for line in lines[1:])]))
    for target in plan.skipped:
        print(f'skipped change {target.number} "{target.change.subject}": {", ".join(target.ruled_out)}')


def _question(plan: VotePlan) -> str:
    """``3 drafts and Code-Review+1 on 2155, 2158 (2156 keeps your vote)``: what the confirmation asks to send."""
    voted = [t for t in plan.sends if plan.votes_on(t)]
    kept = [str(t.number) for t in plan.takes if plan.keeps(t)]
    what = counted(plan.draft_count, "draft")
    if voted:
        what += f" and {format_votes(plan.votes)} on {', '.join(str(t.number) for t in voted)}"
    else:
        what += f" on {', '.join(str(t.number) for t in plan.sends)}"
    if kept:
        what += f" ({', '.join(kept)} {'keeps' if len(kept) == 1 else 'keep'} your vote)"
    return what


def vote(args: argparse.Namespace, p: argparse.ArgumentParser, service: GerritService, cwd: Path) -> int:
    """List your drafts and the votes per change, confirm, and publish them: one REST call per change."""
    votes = parse_votes(args.vote)
    if publish_blocked() and not args.dry_run:
        p.error(f"publishing is blocked: {PUBLISH_GUARD_ENV} is set in the environment")
    changes, state = vote_changes(service, cwd, args.change or [], chain=args.chain)
    if stale := review_moved(changes, state):
        print(stale, file=sys.stderr)
        return int(ExitCode.REFUSED)
    message = read_message(args.message, args.file) or None
    # A chain vote keeps the votes you gave on its changes; naming a change votes over yours.
    plan = plan_vote(service, changes, votes, message, me=service.self_account_id(), keep_yours=args.chain)
    if not args.json_:
        _print_plan(plan)
    if not args.dry_run and not confirm_publish(args, _question(plan)):
        return int(ExitCode.OK)

    def sent(target: VoteTarget) -> None:
        if not args.json_:
            sent_votes = plan.votes_on(target)
            kept = plan.keeps(target)
            print(
                f"published {counted(target.draft_count, 'draft')}"
                + (f" and {format_votes(sent_votes)}" if sent_votes else "")
                + f" on {target.number}"
                + (f" (keeps your {format_votes(kept)})" if kept else "")
            )

    if not args.dry_run:
        send_vote(service, plan, sent=sent)
    if args.json_:
        answer = sent_vote_json(plan, "dry-run" if args.dry_run else "published")
        print(json.dumps(answer["changes"][0] if len(plan.targets) == 1 else answer, indent=2))
    elif state is not None and not args.dry_run:
        say("next: ger review --done" if args.chain else "next: ger review --next · ger review --done", file=sys.stdout)
    return int(ExitCode.OK)
