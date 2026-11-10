"""Read endpoints of ``ger serve``: parse the request, call core, return JSON. Nothing here changes state.

Spec: docu/spec/commands/serve.md.
"""

from __future__ import annotations

import re
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from gerrit_workflow_tools import __version__
from gerrit_workflow_tools.cli_common import failure_exit
from gerrit_workflow_tools.core.annotated_stack import load_annotated_stack, resolve_rev_range
from gerrit_workflow_tools.core.attention import Attention, commit_needs_edit_attention
from gerrit_workflow_tools.core.ci_content import CiContent, introduced_fingerprints
from gerrit_workflow_tools.core.comment_chains import COMMENT_SELECTIONS
from gerrit_workflow_tools.core.commit_diff import EMPTY_TREE, commit_diff, file_text
from gerrit_workflow_tools.core.gerrit.change_resolution import ChangeResolutionError
from gerrit_workflow_tools.core.gerrit.change_view import parse_gerrit_time
from gerrit_workflow_tools.core.gerrit.rest import norm_change_id
from gerrit_workflow_tools.core.gerrit.stack_context import resolve_stack_context
from gerrit_workflow_tools.core.gerrit_change_status import PatchsetStatus
from gerrit_workflow_tools.core.gerrit_show import ShownCommit, load_shown_commits, resolve_show_targets
from gerrit_workflow_tools.core.git_run import commit_sha_or_none
from gerrit_workflow_tools.core.git_state import (
    checked_out_branch_name,
    rebase_in_progress,
    repo_toplevel,
    resolve_working_branch,
)
from gerrit_workflow_tools.core.inbox import load_inbox, pick_headline, section_summary
from gerrit_workflow_tools.core.ready_strategy import blocked_shas_for_stack
from gerrit_workflow_tools.core.review_chain import host_from_web_base
from gerrit_workflow_tools.core.review_checkout import fetch_patchset, find_review
from gerrit_workflow_tools.core.review_delta import load_review_delta
from gerrit_workflow_tools.core.review_target import patchset_to_review, related_chain_numbers
from gerrit_workflow_tools.render.review_json import chain_json, headline_json, iso_utc, review_state_json
from gerrit_workflow_tools.render.show_json import commit_json_payload, log_commit_json
from gerrit_workflow_tools.server.request import BadRequest, Context, Request, safe_rev


API_VERSION = 1
_OBJECT_ID = re.compile(r"[0-9a-f]{7,40}")
_NOT_ON_GERRIT = frozenset({Attention.NOT_PUSHED, Attention.AHEAD_OF_GERRIT})


def error_json(error: BaseException) -> dict[str, str]:
    """``{code, message}`` for a failure: the shared exit-code name, so one reason reads the same everywhere."""
    failure = failure_exit(error)
    return {"code": failure[0].name if failure else "INTERNAL", "message": str(error) or type(error).__name__}


def health(ctx: Context, _req: Request) -> dict[str, Any]:
    """Who is answering, and for which repository."""
    top = repo_toplevel(ctx.cwd)
    return {
        "name": "ger",
        "version": __version__,
        "api": API_VERSION,
        "repo": str(top) if top else None,
        "branch": checked_out_branch_name(ctx.cwd),
        "host": host_from_web_base(ctx.service.web_base),
    }


def stack(ctx: Context, req: Request) -> dict[str, Any]:
    """The **annotated stack** of the working branch, with the ready boundary: ``ger log`` as JSON."""
    cwd, settings = ctx.cwd, ctx.settings
    view = load_annotated_stack(
        cwd,
        resolve_rev_range(cwd, None, settings=settings),
        settings=settings,
        gerrit=ctx.service.rest,
        fetch_ci_pipelines=req.flag("pipelines"),
    )
    context = resolve_stack_context(cwd, settings=settings)
    blocked = (
        blocked_shas_for_stack(
            cwd,
            project=context.project,
            commits=view.rows,
            stop_pattern=settings.stop_pattern,
            settings=settings,
            web_base=settings.gerrit_web_url,
        )
        if view.rows
        else frozenset()
    )
    head = commit_sha_or_none(cwd, "HEAD")
    commits = [
        {
            **log_commit_json(c),
            "short_sha": c.short_sha,
            "abandoned": c.abandoned,
            "updated": iso_utc(parse_gerrit_time(c.updated)),
            "reviewers": [reviewer.slug for reviewer in c.reviewers],
            "is_head": c.sha == head,
            "held_back": c.sha in blocked,
            "needs_edit": commit_needs_edit_attention(c),
            **({"resolution_note": view.notes_by_sha[c.sha]} if c.sha in view.notes_by_sha else {}),
        }
        for c in view.commits
    ]
    return {
        "branch": checked_out_branch_name(cwd),
        "head": head,
        "rebase_in_progress": rebase_in_progress(cwd),
        "stack": {
            "project": context.project,
            "target_branch": context.target_branch,
            "push_branch": context.push_branch,
        },
        "summary": {
            "commits": len(commits),
            "ready": len(commits) - len(blocked),
            "attention": sum(1 for c in view.commits if c.attention_reasons),
            "needs_edit": sum(1 for c in view.commits if commit_needs_edit_attention(c)),
            "not_on_gerrit": sum(1 for c in view.commits if _NOT_ON_GERRIT.intersection(c.attention_reasons)),
        },
        "commits": commits,
    }


def _soft(load: Any, ctx: Context, req: Request) -> dict[str, Any]:
    """*load*'s answer, or ``{"error": …}`` for a known failure: one broken half must not hide the other."""
    try:
        return load(ctx, req)  # type: ignore[no-any-return]
    except Exception as error:  # pylint: disable=broad-exception-caught
        if failure_exit(error) is None:
            raise
        return {"error": error_json(error)}


def _inbox(ctx: Context, _req: Request) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    sections = load_inbox(ctx.service, now=now)
    return {
        "generated": iso_utc(now),
        "headline": headline_json(pick_headline(sections)),
        "sections": [
            {
                "name": section.name,
                "query": section.query,
                "summary": section_summary(section.chains),
                "chains": [chain_json(chain) for chain in section.chains],
            }
            for section in sections
        ],
    }


def overview(ctx: Context, req: Request) -> dict[str, Any]:
    """Both inbox sections, the **headline**, and the local stack beside them.

    Chain members that are commits of the local stack carry ``local_sha``. The inbox and the
    local stack fail separately: either may be ``{"error": …}`` while the other is shown.
    """
    local = _soft(stack, ctx, req)
    inbox = _soft(_inbox, ctx, req)
    sha_by_change = {
        norm_change_id(c["change_id"]): c["sha"] for c in local.get("commits", []) if c.get("change_id") and c["sha"]
    }
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


def _ci_state(item: ShownCommit) -> list[CiContent] | None:
    """The commit's CI content when Gerrit's CI verdict is about this commit, else ``None``."""
    commit = item.commit
    if not commit.pushed or commit.patchset_status == PatchsetStatus.NEWER:
        return None
    return item.ci_contents


def show(ctx: Context, req: Request) -> dict[str, Any]:
    """Overlay, comment chains and (``ci=1``) CI content per target: ``ger show`` as JSON.

    With ``stack=1``, or ``chain=1`` (the ``rev`` values are one chain, base first), each
    commit also carries ``ci_introduced``: fingerprints of its **introduced findings**, or
    ``null`` where that cannot be told.
    """
    selection = req.one("comments", "unresolved")
    if selection not in COMMENT_SELECTIONS:
        raise BadRequest(f"comments must be one of {', '.join(COMMENT_SELECTIONS)}")
    revs = [safe_rev(rev) for rev in req.many("rev")]
    branch = resolve_working_branch(ctx.cwd, settings=ctx.settings)
    targets = resolve_show_targets(
        ctx.cwd, revs, ctx.service.rest, settings=ctx.settings, stack=req.flag("stack"), branch=branch
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
    introduced = introduced_fingerprints([_ci_state(item) for item in shown]) if is_chain else None
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
        payload["updated"] = iso_utc(parse_gerrit_time(item.commit.updated))
        if introduced is not None:
            found = introduced[index]
            payload["ci_introduced"] = sorted(found) if found is not None else None
        commits.append(payload)
    return {"commits": commits}


def _review_delta(ctx: Context, req: Request, rev: str) -> dict[str, Any]:
    since = None if req.one("since") == "reviewed" else req.number("since")
    delta = load_review_delta(ctx.service, ctx.cwd, rev, since=since)
    return {
        "commit": delta.commit,
        "base": delta.base,
        "local_commit": False,
        "files": [asdict(change) for change in delta.files],
        "since": {
            "patch_set": delta.reviewed_patch_set,
            "current_patch_set": delta.current_patch_set,
            "up_to_date": delta.up_to_date,
            "rebased": delta.rebased,
        },
    }


def diff(ctx: Context, req: Request) -> dict[str, Any]:
    """The files one change touched, against its first parent. Fetches the patchset when it is not here.

    With ``since=reviewed`` (or ``since=<patchset>``) it is the **review delta** instead: the
    current patchset against the one you last reviewed.
    """
    rev = safe_rev(req.one("rev"))
    if req.one("since"):
        return _review_delta(ctx, req, rev)
    branch = resolve_working_branch(ctx.cwd, settings=ctx.settings)
    [target] = resolve_show_targets(ctx.cwd, [rev], ctx.service.rest, settings=ctx.settings, branch=branch)
    sha = target.row.sha
    if not sha or commit_sha_or_none(ctx.cwd, sha) is None:
        patchset, _url = patchset_to_review(ctx.service, ctx.cwd, rev, None)
        fetch_patchset(ctx.cwd, ctx.settings.gerrit_remote, patchset.number, patchset.patch_set, patchset.commit)
        sha = patchset.commit
    changed = commit_diff(ctx.cwd, sha)
    return {
        "commit": changed.commit,
        "base": changed.base,
        "subject": target.row.subject,
        "local_commit": target.is_local_commit,
        "files": [asdict(change) for change in changed.files],
    }


def file(ctx: Context, req: Request) -> dict[str, Any]:
    """Text of ``path`` at commit ``rev`` (an object id from ``/diff``); ``content`` is ``null`` when absent."""
    rev, path = req.one("rev") or "", req.one("path") or ""
    if not _OBJECT_ID.fullmatch(rev):
        raise BadRequest("rev must be a commit id, as /diff returns it")
    if not path or path.startswith(("-", "/")) or "\0" in path:
        raise BadRequest("path is missing or not a repository path")
    content = None if rev == EMPTY_TREE else file_text(ctx.cwd, rev, path)
    return {"rev": rev, "path": path, "content": content}


def review(ctx: Context, _req: Request) -> dict[str, Any]:
    """The **review state** of this checkout or its review worktree; ``review`` is ``null`` when there is none."""
    state = find_review(ctx.cwd, ctx.settings)
    return {"review": review_state_json(state, chain=None, url=None) if state else None}
