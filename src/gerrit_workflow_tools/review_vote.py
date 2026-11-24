"""What ``ger review --vote`` lists, asks and sends: your drafts and the votes, on the change under review.

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
from gerrit_workflow_tools.core.gerrit_patchset import resolve_patchset
from gerrit_workflow_tools.core.review_checkout import ReviewUsageError, find_review
from gerrit_workflow_tools.core.votes import parse_votes
from gerrit_workflow_tools.render.comments import drafts_in, format_pending_drafts


def _vote_target(args: argparse.Namespace, service: GerritService, cwd: Path) -> tuple[str, int, int | None]:
    """``(triplet, number, reviewed patchset)`` to vote on: ``--change``, else the review going on."""
    if args.change is not None:
        target, _url = resolve_patchset(service, cwd, args.change, None)
        return target.key, target.number, None
    state = find_review(cwd, service.settings)
    if state is None:
        raise ReviewUsageError("no review going on here: name the change with --change")
    return state.key, state.number, state.patch_set


def vote(args: argparse.Namespace, p: argparse.ArgumentParser, service: GerritService, cwd: Path) -> int:
    """List your drafts and the votes on the change, confirm, and publish them in one REST call."""
    votes = parse_votes(args.vote)
    if publish_blocked() and not args.dry_run:
        p.error(f"publishing is blocked: {PUBLISH_GUARD_ENV} is set in the environment")
    key, number, reviewed = _vote_target(args, service, cwd)
    # Only a review going on names a patchset the vote could miss; --change was just read from Gerrit.
    current = reviewed and ChangeView.from_payload(service.rest.get_change(key)).current_revision_number
    if current != reviewed:
        print(
            f"change {number} is at patchset {current}, you reviewed {reviewed}: run ger review {number} first",
            file=sys.stderr,
        )
        return int(ExitCode.REFUSED)
    chains = service.comments.chains_with_drafts(key)
    count = len(drafts_in(chains))
    voted = ", ".join(f"{label}{value:+d}" for label, value in votes.items())
    if not args.json_:
        print("\n".join(format_pending_drafts(f"change {number}: {count} draft(s), {voted}", chains)))
    if args.dry_run:
        return int(ExitCode.OK)
    if not confirm_publish(args, f"{count} draft(s) and {voted} on {number}"):
        return int(ExitCode.REFUSED)
    message = "\n\n".join(args.message) if args.message else None
    service.comments.publish_drafts(key, message=message, labels=votes)
    if args.json_:
        print(json.dumps({"change": key, "drafts": count, "labels": votes, "status": "published"}, indent=2))
    else:
        print(f"published {count} draft(s) and {voted} on {number}")
    return int(ExitCode.OK)
