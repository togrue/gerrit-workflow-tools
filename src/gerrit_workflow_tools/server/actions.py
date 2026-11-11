"""Endpoints of ``ger serve`` that change something: the review checkout, draft replies, the local stack.

None of them publishes to Gerrit: replies are drafts, and publishing stays with ``ger push`` /
``ger review --vote``, in front of a human. Spec: docu/spec/commands/serve.md.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from gerrit_workflow_tools.core.agent_runbook import set_hold
from gerrit_workflow_tools.core.annotated_stack import annotate
from gerrit_workflow_tools.core.comment_anchor import LocationError
from gerrit_workflow_tools.core.comment_target import resolve_comment_target
from gerrit_workflow_tools.core.comment_write import default_state, locate, write_comment
from gerrit_workflow_tools.core.gerrit.change_resolution import ChangeResolutionError
from gerrit_workflow_tools.core.gerrit_change_status import CommentState
from gerrit_workflow_tools.core.gerrit_patchset import resolve_patchset
from gerrit_workflow_tools.core.gerrit_show import gerrit_rest_key, resolve_show_targets
from gerrit_workflow_tools.core.git_state import checked_out_branch_name, resolve_working_branch
from gerrit_workflow_tools.core.reorder import STEP_ACTIONS, StackStep, rewrite_stack, undo_rewrite
from gerrit_workflow_tools.core.review_checkout import (
    CheckoutOptions,
    ReviewUsageError,
    close_review,
    find_review,
    open_review,
)
from gerrit_workflow_tools.core.review_target import related_chain_numbers
from gerrit_workflow_tools.core.upstream import set_branch_upstream
from gerrit_workflow_tools.render.review_json import review_state_json
from gerrit_workflow_tools.render.show_json import written_comment_json
from gerrit_workflow_tools.server import views
from gerrit_workflow_tools.server.request import BadRequest, Context, Request, safe_rev


_REVIEW_MODES = ("worktree", "branch")


def review_open(ctx: Context, req: Request) -> dict[str, Any]:
    """Check a change out for review (``ger review CHANGEISH``); answers the new **review state**.

    Body: ``change`` (number or changeish), optional ``patchset``, ``mode`` (``worktree`` /
    ``branch``; default ``ger.reviewCheckout``, else ``worktree``), ``stage``, ``force``.
    """
    change = req.body_field("change", (str, int), required=True)
    mode = req.body_field("mode", str) or ctx.settings.review_checkout or "worktree"
    if mode not in _REVIEW_MODES:
        raise BadRequest(f"mode must be one of {', '.join(_REVIEW_MODES)}")
    stage = req.body_field("stage", bool)
    target, url = resolve_patchset(
        ctx.service, ctx.cwd, safe_rev(str(change), "change"), req.body_field("patchset", int)
    )
    options = CheckoutOptions(
        mode="worktree" if mode == "worktree" else "branch",
        stage=ctx.settings.review_stage if stage is None else stage,
        force=bool(req.body_field("force", bool)),
    )
    state, moved_from = open_review(ctx.cwd, target, settings=ctx.settings, options=options)
    chain = related_chain_numbers(ctx.service, state.key, state.patch_set)
    return {"review": review_state_json(state, chain=chain, url=url), "moved_from": moved_from}


def review_done(ctx: Context, _req: Request) -> dict[str, Any]:
    """Leave the review (``ger review --done``): which ``change-<n>`` branches went, which stayed."""
    state = find_review(ctx.cwd, ctx.settings)
    if state is None:
        raise ReviewUsageError("no review going on here")
    deleted, kept = close_review(state)
    return {"done": state.number, "deleted": deleted, "kept": kept}


def reply(ctx: Context, req: Request) -> dict[str, Any]:
    """Answer a comment chain with a draft (``ger reply``); a draft of yours there is updated.

    Body: ``rev`` (the change), ``comment_id`` (any comment of the chain), ``message``, and
    optional ``resolved`` (omit to leave the chain as it is).
    """
    rev = safe_rev(req.body_field("rev", str, required=True))
    comment_id = req.body_field("comment_id", str, required=True)
    message = req.body_field("message", str, required=True).strip()
    resolved = req.body_field("resolved", bool)
    if not message:
        raise BadRequest("message is empty")
    branch = resolve_working_branch(ctx.cwd, settings=ctx.settings)
    [target] = resolve_show_targets(ctx.cwd, [rev], ctx.service.rest, settings=ctx.settings, branch=branch)
    [commit] = annotate([target.row], service=ctx.service, cwd=ctx.cwd).require_gerrit()
    key = gerrit_rest_key(commit, target.resolution, ctx.service)
    matches = ctx.service.comments.find_chain({key: commit.freshness}, comment_id) if commit.pushed and key else []
    if not matches:
        raise ChangeResolutionError(f"no comment {comment_id!r} on {rev!r}")
    match = matches[0]
    saved = ctx.service.comments.save_reply(
        match.change, match.chain, message, unresolved=None if resolved is None else not resolved
    )
    return {"change": match.change, "root_id": match.chain.root_id, "reply": asdict(saved)}


def hold(ctx: Context, req: Request) -> dict[str, Any]:
    """Put a comment chain on **hold** (keep it from the agent runbook), or release it. Local only."""
    root_id = req.body_field("root_id", str, required=True)
    held = req.body_field("held", bool, required=True)
    return {"root_id": root_id, "held": held, "holds": sorted(set_hold(ctx.cwd, root_id, held))}


def _steps(raw: list[Any]) -> list[StackStep]:
    steps: list[StackStep] = []
    for item in raw:
        if isinstance(item, str):
            steps.append(StackStep(item))
            continue
        sha, action = (item.get("sha"), item.get("action", "pick")) if isinstance(item, dict) else (None, None)
        if not isinstance(sha, str) or action not in STEP_ACTIONS:
            raise BadRequest(f"a step is a commit id or {{sha, action}} with action one of {', '.join(STEP_ACTIONS)}")
        steps.append(StackStep(sha, action))
    return steps


def rewrite(ctx: Context, req: Request) -> dict[str, Any]:
    """Rewrite the local stack as ``steps`` say (oldest first), all or nothing; answers the new stack.

    A step is a commit id (kept) or ``{sha, action}``. ``previous_head`` in the answer is
    what ``/stack/undo`` takes back.
    """
    done = rewrite_stack(ctx.cwd, _steps(req.body_field("steps", list, required=True)), settings=ctx.settings)
    return {**views.stack(ctx, Request()), "previous_head": done.previous_head}


def undo(ctx: Context, req: Request) -> dict[str, Any]:
    """Take a rewrite back: ``head`` (the stack's head after it) returns to ``previous_head``."""
    head = safe_rev(req.body_field("head", str, required=True), "head")
    previous = safe_rev(req.body_field("previous_head", str, required=True), "previous_head")
    undo_rewrite(ctx.cwd, head, previous)
    return views.stack(ctx, Request())


def set_upstream(ctx: Context, req: Request) -> dict[str, Any]:
    """Make ``upstream`` (a remote-tracking branch, ``origin/main``) the upstream of the working branch.

    Answers ``branch`` and its new ``upstream``; ``GET /stack`` can show the stack from then on.
    """
    upstream = safe_rev(req.body_field("upstream", str, required=True).strip(), "upstream")
    branch = checked_out_branch_name(ctx.cwd)
    if branch is None:
        raise BadRequest("HEAD is detached: there is no branch to give an upstream")
    set_branch_upstream(ctx.cwd, branch, upstream)
    return {"branch": branch, "upstream": upstream}


_NEW_COMMENT_STATES: tuple[CommentState, ...] = ("draft", "local-draft", "note")


def new_comment(ctx: Context, req: Request) -> dict[str, Any]:
    """Start a comment chain (``ger comment``): a draft on Gerrit, a local draft or a note.

    Body: ``rev`` (the change), ``location`` (``PATH``, ``PATH:LINE``, ``PATH:START-END`` or
    ``PATH:L.C-L.C``, lines from 1), ``message``, and optional ``state`` (default as
    ``ger comment``: a draft where Gerrit has the change, else a local draft) and ``resolved``.
    """
    rev = safe_rev(req.body_field("rev", str, required=True))
    message = req.body_field("message", str, required=True).strip()
    state = req.body_field("state", str)
    if not message:
        raise BadRequest("message is empty")
    if state is not None and state not in _NEW_COMMENT_STATES:
        raise BadRequest(f"state must be one of {', '.join(_NEW_COMMENT_STATES)}")
    branch = resolve_working_branch(ctx.cwd, settings=ctx.settings)
    target = resolve_comment_target(ctx.service, ctx.cwd, rev, branch=branch)
    try:
        location = locate(ctx.cwd, req.body_field("location", str, required=True), target)
    except LocationError as error:
        raise BadRequest(str(error)) from error
    chosen: CommentState = default_state(target, gerrit=True)
    for known in _NEW_COMMENT_STATES:
        chosen = known if state == known else chosen
    resolved = bool(req.body_field("resolved", bool))
    written = write_comment(ctx.cwd, ctx.service, target, location, message, state=chosen, unresolved=not resolved)
    return written_comment_json(written)
