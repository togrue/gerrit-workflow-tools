"""``ger serve``'s vote: what it would send (``GET /vote``), and sending it (``POST /vote``).

``ger review --vote`` with the choices made in the editor: the same core plans and sends
(``core.review_target``: ``plan_vote``, ``send_vote``).
The only endpoint that publishes to Gerrit; the client asks a person first, as the command does.
Spec: docu/spec/commands/serve.md.
"""

from __future__ import annotations

from typing import Any

from gerrit_workflow_tools.core.gerrit.change_view import ChangeView
from gerrit_workflow_tools.core.review_target import (
    VoteMoved,
    moved_since_preview,
    plan_vote,
    send_vote,
    vote_changes,
    vote_options,
)
from gerrit_workflow_tools.core.votes import parse_votes
from gerrit_workflow_tools.render.review_json import sent_vote_json, vote_plan_json
from gerrit_workflow_tools.server.request import BadRequest, Context, Request, safe_rev


def _changes(ctx: Context, change: str | int, *, chain: bool) -> list[ChangeView]:
    """Change *change*, or with *chain* every open change of its review chain, as Gerrit has them now."""
    return vote_changes(ctx.service, ctx.cwd, [safe_rev(str(change), "change")], chain=chain)[0]


def vote_plan(ctx: Context, req: Request) -> dict[str, Any]:
    """What a vote on ``change`` (``chain=1``: every open change of its chain) would send; nothing is sent.

    Per change: your drafts on it, what you may vote, whether it is yours, your votes on it. ``options``
    are the votes to offer, each with what it means, which changes take it and (a chain) which
    keep your vote on its label. ``vote`` (as
    ``ger review --vote`` takes it, repeatable) fills ``ruled_out`` per change.
    """
    change = req.one("change")
    if change is None:
        raise BadRequest("missing parameter 'change'")
    chain = req.flag("chain")
    changes = _changes(ctx, change, chain=chain)
    votes = parse_votes(req.many("vote"))
    plan = plan_vote(ctx.service, changes, votes, me=ctx.service.self_account_id(), keep_yours=chain)
    return vote_plan_json(plan, vote_options(plan.targets, keep_yours=plan.keep_yours), web_base=ctx.service.web_base)


def _votes(raw: dict[str, Any]) -> dict[str, int]:
    if not all(isinstance(label, str) and type(value) is int for label, value in raw.items()):
        raise BadRequest("votes is {label: value}, each value a whole number")
    return dict(raw)


def _previewed(raw: dict[str, Any]) -> dict[int, int]:
    try:
        return {int(number): int(patch_set) for number, patch_set in raw.items() if type(patch_set) is int}
    except ValueError as error:
        raise BadRequest("patch_sets is {change number: patchset}, as GET /vote answered") from error


def vote(ctx: Context, req: Request) -> dict[str, Any]:
    """Publish your drafts with ``votes`` and ``message`` on ``change`` (``chain``: its whole chain).

    ``patch_sets`` is what ``GET /vote`` showed (change number: patchset): when the changes or a
    patchset are not what was shown, nothing is sent (``REFUSED``). ``votes`` may be empty: your
    drafts go out without one. Answers as ``ger review --vote --json`` does for several changes.
    """
    change = req.body_field("change", (str, int), required=True)
    votes = _votes(req.body_field("votes", dict) or {})
    message = (req.body_field("message", str) or "").strip() or None
    previewed = _previewed(req.body_field("patch_sets", dict, required=True))
    chain = bool(req.body_field("chain", bool))
    changes = _changes(ctx, change, chain=chain)
    if moved := moved_since_preview(changes, previewed):
        raise VoteMoved(moved)
    plan = plan_vote(ctx.service, changes, votes, message, me=ctx.service.self_account_id(), keep_yours=chain)
    send_vote(ctx.service, plan)
    return sent_vote_json(plan, "published")
