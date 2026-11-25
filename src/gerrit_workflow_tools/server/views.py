"""Read endpoints of ``ger serve``: parse the request, call core, return JSON. Nothing here changes state.

Spec: docu/spec/commands/serve.md.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from functools import partial
from itertools import pairwise
from typing import Any, TypeVar, cast

from gerrit_workflow_tools import __version__
from gerrit_workflow_tools.cli_common import failure_exit
from gerrit_workflow_tools.core.agent_runbook import load_holds, plan_runbook
from gerrit_workflow_tools.core.annotated_stack import load_stack_overview
from gerrit_workflow_tools.core.attention import Attention, commit_needs_edit_attention
from gerrit_workflow_tools.core.ci_content import introduced_fingerprints
from gerrit_workflow_tools.core.comment_chains import COMMENT_SELECTIONS
from gerrit_workflow_tools.core.comment_write import new_comment_state
from gerrit_workflow_tools.core.config_keys import legacy_keys_notice
from gerrit_workflow_tools.core.gerrit.change_resolution import ChangeResolutionError
from gerrit_workflow_tools.core.gerrit.change_view import parse_gerrit_time
from gerrit_workflow_tools.core.gerrit.rest import norm_change_id
from gerrit_workflow_tools.core.gerrit_show import load_shown_commits, resolve_show_targets
from gerrit_workflow_tools.core.git_run import commit_sha_or_none
from gerrit_workflow_tools.core.inbox import RebaseHeadline, load_inbox, pick_headline, project_scope
from gerrit_workflow_tools.core.push_checks import push_blocked
from gerrit_workflow_tools.core.rebase_state import rebase_underway
from gerrit_workflow_tools.core.reorder_preview import PreviewUnavailable, swap_conflicts
from gerrit_workflow_tools.core.review_chain import host_from_web_base
from gerrit_workflow_tools.core.review_checkout import find_review, staged_review
from gerrit_workflow_tools.core.review_target import drafts_published_by, related_chain_numbers
from gerrit_workflow_tools.core.stack import (
    get_stack_snapshot,
    merge_commits,
    pinned_below,
    pinned_count,
    rewrite_base,
)
from gerrit_workflow_tools.core.upstream import branch_has_upstream, upstream_choice
from gerrit_workflow_tools.render.agent_runbook import runbook_json
from gerrit_workflow_tools.render.review_json import (
    chain_json,
    headline_json,
    iso_utc,
    review_state_json,
    sections_json,
)
from gerrit_workflow_tools.render.show_json import (
    commit_json_payload,
    log_commit_json,
    next_steps_json,
    rebase_json,
    rebase_steps,
    upstream_json,
)
from gerrit_workflow_tools.render.show_markdown import commit_markdown
from gerrit_workflow_tools.render.suggested_fixes import comment_text
from gerrit_workflow_tools.server.diff_views import commit_files
from gerrit_workflow_tools.server.request import BadRequest, Context, Request, safe_rev


API_VERSION = "1.2.0"
"""The serve contract's version, ``MAJOR.MINOR.PATCH`` (bump rules: ``docu/spec/commands/serve.md``)."""
_T = TypeVar("_T")
_NOT_ON_GERRIT = frozenset({Attention.NOT_PUSHED, Attention.AHEAD_OF_GERRIT, Attention.DIVERGED})


def error_json(error: BaseException) -> dict[str, str]:
    """``{code, message}`` for a failure: the shared exit-code name, so one reason reads the same everywhere.

    A failure the client can do something about also carries ``reason``, a word for it.
    """
    failure = failure_exit(error)
    answer = {"code": failure[0].name if failure else "INTERNAL", "message": str(error) or type(error).__name__}
    reason = getattr(error, "reason", None)
    return {**answer, "reason": reason} if isinstance(reason, str) else answer


def health(ctx: Context, _req: Request) -> dict[str, Any]:
    """Who is answering, for which repository, whether Gerrit's event stream is followed, and what
    in this repository's configuration ger warns about (old key names), with the command that fixes it."""
    top = ctx.run.repo.root
    legacy = legacy_keys_notice(ctx.settings.values, ctx.settings.scopes)
    return {
        "name": "ger",
        "version": __version__,
        "api": API_VERSION,
        "repo": str(top) if top else None,
        "branch": ctx.run.worktree.branch,
        "host": host_from_web_base(ctx.service.web_base),
        "warnings": [{"message": legacy.message, "argv": list(legacy.argv) if legacy.argv else None}] if legacy else [],
        **ctx.activity.json(),
    }


def stack(ctx: Context, req: Request) -> dict[str, Any]:
    """The **annotated stack** of the working branch, with the ready boundary: ``ger log`` as JSON."""
    return _stack(ctx, req)[0]


def _stack(ctx: Context, req: Request) -> tuple[dict[str, Any], RebaseHeadline | None]:
    """:func:`stack`, and the rebase underway as the headline it makes: ``None`` without one."""
    cwd = ctx.cwd
    overview = load_stack_overview(ctx.run, fetch_ci_pipelines=req.flag("pipelines"))
    view, blocked, progress = overview.view, overview.blocked, overview.progress
    context = ctx.run.repo.shown_stack
    head = commit_sha_or_none(cwd, "HEAD")
    steps = rebase_steps(view)
    merges = merge_commits(cwd, [c.sha for c in view.commits])
    pinned = pinned_below([c.sha for c in view.commits], merges)
    commits = [
        {
            **log_commit_json(c),
            "short_sha": c.short_sha,
            "abandoned": c.abandoned,
            "diverged": c.diverged,
            "merge": c.sha in merges,
            "pinned": index < pinned,
            "rebase": steps[index],
            "updated": iso_utc(parse_gerrit_time(c.updated)),
            "is_head": c.sha == head,
            "held_back": c.sha in blocked,
            "needs_edit": commit_needs_edit_attention(c),
            **({"resolution_note": view.notes_by_sha[c.sha]} if c.sha in view.notes_by_sha else {}),
        }
        for index, c in enumerate(view.commits)
    ]
    underway = rebase_underway(ctx.run.worktree.repository_dir)
    stopped_at = next((c for c, step in zip(view.commits, steps, strict=True) if step == "current"), None)
    rebase = RebaseHeadline(progress.stop, stopped_at) if progress else None
    answer = {
        "branch": ctx.run.worktree.branch,
        "head": head,
        "rebase_in_progress": underway,
        "rebase": rebase_json(view, progress),
        "upstream": upstream_json(overview.moved),
        "push_blocked": _push_blocked_json(ctx),
        "staged_review": _staged_review_json(ctx),
        "stack": {
            "project": context.project,
            "target_branch": context.target_branch or None,
            "push_branch": context.push_branch or None,
        },
        "summary": {
            "commits": len(commits),
            "ready": overview.ready,
            "attention": sum(1 for c in view.commits if c.attention_reasons),
            "needs_edit": sum(1 for c in view.commits if commit_needs_edit_attention(c)),
            "not_on_gerrit": sum(1 for c in view.commits if _NOT_ON_GERRIT.intersection(c.attention_reasons)),
        },
        "commits": commits,
        "next": next_steps_json(overview),
        **({"gerrit_unavailable": error_json(view.gerrit_unavailable)} if view.gerrit_unavailable else {}),
    }
    return answer, rebase


def _staged_review_json(ctx: Context) -> dict[str, object] | None:
    """The **staged review** in this checkout, or ``None``: what ``ger push``, ``ger edit`` and the
    other rewrites refuse over."""
    state = staged_review(ctx.run.worktree.repository_dir, ctx.cwd)
    return review_state_json(state, chain=None, url=None) if state else None


def _push_blocked_json(ctx: Context) -> dict[str, str] | None:
    """Why ``ger push`` would refuse whatever the stack holds (a rebase, a ``git am`` underway), or ``None``."""
    blocked = push_blocked(ctx.run.worktree.repository_dir, ctx.run.repo.working_branch)
    return {"reason": blocked.by.value, "message": blocked.message} if blocked else None


def stack_moves(ctx: Context, _req: Request) -> dict[str, Any]:
    """Which adjacent commits of the local stack can swap places: a conflict preview, nothing is touched.

    One entry per adjacent pair above the newest merge, oldest first: that merge and what is below
    it stay where they are. ``available`` is false when this git cannot tell.
    """
    snapshot = get_stack_snapshot(ctx.cwd, settings=ctx.settings)
    pinned = pinned_count(ctx.cwd, snapshot.commits)
    commits = snapshot.commits[pinned:]
    base = rewrite_base(ctx.cwd, snapshot, pinned)
    answer: dict[str, Any] = {"head": commit_sha_or_none(ctx.cwd, "HEAD"), "available": True, "swaps": []}
    try:
        conflicts = swap_conflicts(ctx.cwd, base, commits)
    except PreviewUnavailable as error:
        return {**answer, "available": False, "reason": str(error)}
    for (lower, upper), conflict in zip(pairwise(commits), conflicts, strict=True):
        found = conflict and {
            "sha": conflict.commit.sha,
            "summary": conflict.commit.subject,
            "files": list(conflict.files),
        }
        answer["swaps"].append({"lower": lower.sha, "upper": upper.sha, "conflict": found})
    return answer


def stack_upstream(ctx: Context, _req: Request) -> dict[str, Any]:
    """The working branch's upstream, and which to offer when it has none: the best guess first."""
    branch = ctx.run.worktree.branch
    if branch is None:
        return {"branch": None, "upstream": None, "suggested": None, "why": None, "candidates": []}
    choice = upstream_choice(ctx.cwd, branch, settings=ctx.settings)
    return {
        "branch": branch,
        "upstream": choice.current,
        "suggested": choice.suggested,
        "why": choice.why,
        "candidates": choice.candidates,
    }


def _soft(load: Callable[[], _T], failed: Callable[[dict[str, Any]], _T]) -> _T:
    """*load*'s answer, or *failed* of ``{"error": …}`` for a known failure: one broken half must not hide the other."""
    try:
        return load()
    except Exception as error:  # pylint: disable=broad-exception-caught
        if failure_exit(error) is None:
            raise
        return failed({"error": error_json(error)})


def _inbox(ctx: Context, req: Request, *, current: set[str], rebase: RebaseHeadline | None) -> dict[str, Any]:
    """Both inbox sections, narrowed to the repository's project; every project with ``all=1``.

    *current* is the local stack's Change-Ids: failed CI is the headline only on that chain.
    *rebase*, a rebase underway in the checkout, is the headline before anything of theirs.
    """
    now = datetime.now(timezone.utc)
    projects = project_scope(ctx.cwd, ctx.settings, all_projects=req.flag("all"))
    sections = load_inbox(ctx.service, now=now, projects=projects, all_projects=not projects)
    return {
        "generated": iso_utc(now),
        "projects": projects,
        "headline": headline_json(pick_headline(sections, current=current, rebase=rebase)),
        "sections": sections_json(sections),
    }


def overview(ctx: Context, req: Request) -> dict[str, Any]:
    """Both inbox sections, the **headline**, and the local stack beside them.

    Failed CI makes the headline only on the local stack's own chain. Chain members that are
    commits of the local stack carry ``local_sha``. The inbox and the
    local stack fail separately: either may be ``{"error": …}`` while the other is shown.
    """
    local, rebase = _soft(partial(_stack, ctx, req), lambda failure: (failure, None))
    if "error" in local:
        branch = ctx.run.worktree.branch
        local["needs_upstream"] = branch is not None and not branch_has_upstream(ctx.cwd, branch)
    sha_by_change = {
        norm_change_id(c["change_id"]): c["sha"] for c in local.get("commits", []) if c.get("change_id") and c["sha"]
    }
    inbox = _soft(partial(_inbox, ctx, req, current=set(sha_by_change), rebase=rebase), lambda failure: failure)
    if "error" in inbox and rebase is not None:
        # The inbox could not be read; a rebase underway still leads.
        inbox["headline"] = headline_json(rebase)
    for section in inbox.get("sections", []):
        for chain in section["chains"]:
            for member in chain["members"]:
                member["local_sha"] = sha_by_change.get(norm_change_id(member["change_id"]))
            chain["local"] = any(member["local_sha"] for member in chain["members"])
    return {"host": host_from_web_base(ctx.service.web_base), **inbox, "local": local}


def chain(ctx: Context, req: Request) -> dict[str, Any]:
    """The **review chain** change ``<number>`` sits in, base to top."""
    raw = req.path_args[0]
    if not raw.isdigit():
        raise BadRequest(f"not a change number: {raw!r}")
    number = int(raw)
    numbers = related_chain_numbers(ctx.service, raw) or [number]
    chains = ctx.service.fetch_review_chains(" OR ".join(f"change:{n}" for n in numbers))
    found = next((c for c in chains if any(m.number == number for m in c.members)), None)
    if found is None:
        raise ChangeResolutionError(f"change {number} is not open, or not visible to you")
    return chain_json(found)


def show(ctx: Context, req: Request) -> dict[str, Any]:
    """Overlay, comment chains and (``ci=1``) CI content per target: ``ger show`` as JSON.

    With ``stack=1``, or ``chain=1`` (the ``rev`` values are one chain, base first), each
    commit also carries ``ci_introduced``: fingerprints of its **introduced findings**, or
    ``null`` where that cannot be told. ``markdown=1`` adds ``markdown``: what ``ger show --ai`` prints.
    ``files=1`` adds ``files``: what ``/diff`` answers for the commit, so a viewer asks once.
    """
    selection = req.one("comments", "unresolved")
    if selection not in COMMENT_SELECTIONS:
        raise BadRequest(f"comments must be one of {', '.join(COMMENT_SELECTIONS)}")
    revs = [safe_rev(rev) for rev in req.many("rev")]
    branch = ctx.run.repo.working_branch
    targets = resolve_show_targets(
        ctx.cwd,
        revs,
        ctx.service.rest,
        settings=ctx.settings,
        stack=req.flag("stack"),
        branch=branch,
        changes=ctx.service.changes,
    )
    if not targets:
        raise ChangeResolutionError("no commits to show")
    shown = load_shown_commits(
        targets,
        service=ctx.service,
        comments=selection,  # type: ignore[arg-type]
        context_padding=req.number("context"),
        fetch_ci_pipelines=req.flag("pipelines"),
        ci=ctx.ci() if req.flag("ci") else None,
    )
    is_chain = req.flag("ci") and (req.flag("stack") or req.flag("chain"))
    introduced = introduced_fingerprints([item.ci_verdict_contents for item in shown]) if is_chain else None
    holds = load_holds(ctx.cwd)
    me = ctx.service.self_account_id() if any(item.commit.pushed for item in shown) else None
    commits = []
    for index, item in enumerate(shown):
        payload = commit_json_payload(
            item.commit,
            is_local=item.target.is_local_commit,
            resolution=item.target.resolution,
            chains=item.chains,
            ci_content=item.ci_contents,
        )
        payload["short_sha"] = item.commit.short_sha
        payload["new_comment"] = new_comment_state(item.commit, is_local=item.target.is_local_commit)
        payload["own"] = item.commit.owner_account_id == me if item.commit.pushed and me is not None else None
        payload["drafts_published_by"] = drafts_published_by(item.commit, is_local=item.target.is_local_commit, me=me)
        served_chains = cast(list[dict[str, Any]], payload["comment_chains"])
        for chain_payload, chain in zip(served_chains, item.chains, strict=True):
            chain_payload["held"] = chain_payload["root_id"] in holds
            for entry, comment in zip(chain_payload["comments"], chain.comments, strict=True):
                entry["text"] = comment_text(comment.message, comment.suggested_edits)
                entry["markdown"] = comment_text(comment.message, comment.suggested_edits, markdown=True)
        payload["updated"] = iso_utc(parse_gerrit_time(item.commit.updated))
        if req.flag("markdown"):
            lines = commit_markdown(
                item.commit,
                chains=item.chains,
                ci_contents=item.ci_contents,
                comments=selection,  # type: ignore[arg-type]
            )
            payload["markdown"] = "".join(f"{line}\n" for line in lines)
        if req.flag("files"):
            target = item.target
            selected = target.resolution.selected
            payload["files"] = commit_files(
                ctx,
                selected.triplet if selected is not None else target.row.sha,
                sha=target.row.sha,
                subject=target.row.subject,
                is_local=target.is_local_commit,
            )
        if introduced is not None:
            found = introduced[index]
            payload["ci_introduced"] = sorted(found) if found is not None else None
        commits.append(payload)
    return {"commits": commits}


def agent_runbook(ctx: Context, req: Request) -> dict[str, Any]:
    """The **agent runbook** for the local stack's attention walk: Markdown, and its counts.

    ``rev`` (a commit id or Change-Id of the stack) narrows it to one commit. Fetches CI content.
    """
    only = req.one("rev")
    branch = ctx.run.repo.working_branch
    targets = resolve_show_targets(
        ctx.cwd, [], ctx.service.rest, settings=ctx.settings, stack=True, branch=branch, changes=ctx.service.changes
    )
    shown = load_shown_commits(targets, service=ctx.service, context_padding=3, ci=ctx.ci()) if targets else []
    stops = plan_runbook(shown, load_holds(ctx.cwd), only=safe_rev(only) if only else None)
    return runbook_json(stops, branch=ctx.run.worktree.branch)


def review(ctx: Context, _req: Request) -> dict[str, Any]:
    """The **review state** of this checkout or its review worktree; ``review`` is ``null`` when there is none."""
    state = find_review(ctx.cwd, ctx.settings)
    return {"review": review_state_json(state, chain=None, url=None) if state else None}
