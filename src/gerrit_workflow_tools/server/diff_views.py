"""Read endpoints of ``ger serve`` about what a change changed: its diff, its patchsets, a file's text.

Spec: docu/spec/commands/serve.md.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from gerrit_workflow_tools.core.commit_diff import EMPTY_TREE, file_text, show_commit_diff
from gerrit_workflow_tools.core.gerrit_patchset import fetch_patchset, resolve_change, resolve_patchset
from gerrit_workflow_tools.core.gerrit_show import resolve_show_targets
from gerrit_workflow_tools.core.git_run import commit_sha_or_none
from gerrit_workflow_tools.core.review_delta import last_reviewed_patch_set, load_review_delta
from gerrit_workflow_tools.render.review_json import iso_utc
from gerrit_workflow_tools.server.request import OBJECT_ID, BadRequest, Context, Request, safe_rev


def _review_delta(ctx: Context, req: Request, rev: str) -> dict[str, Any]:
    raw = req.one("since")
    local = req.one("local")
    if local is not None and (not OBJECT_ID.fullmatch(local) or (local := commit_sha_or_none(ctx.cwd, local)) is None):
        raise BadRequest("local must be the id of a commit here")
    delta = load_review_delta(
        ctx.service,
        ctx.cwd,
        rev,
        since=None if raw == "reviewed" else "current" if raw == "current" else req.number("since"),
        upto=req.number("patchset"),
        local_commit=local,
    )
    return {
        "commit": delta.commit,
        "base": delta.base,
        "local_commit": delta.target_patch_set is None,
        "files": [asdict(change) for change in delta.files],
        "since": {
            "patch_set": delta.reviewed_patch_set,
            "current_patch_set": delta.current_patch_set,
            "to_patch_set": delta.target_patch_set,
            "up_to_date": delta.up_to_date,
            "identical": delta.identical,
            "rebased": delta.rebased,
        },
    }


def diff(ctx: Context, req: Request) -> dict[str, Any]:
    """The files one change touched, against its first parent. Fetches the patchset when it is not here.

    ``patchset=<n>`` shows that patchset of the change instead of the commit ``rev`` names.

    With ``since=reviewed`` (or ``since=<patchset>``, ``since=current``) it is the **review
    delta** instead: that patchset against the current one, against ``patchset=<n>``, or
    against the local commit ``local=<commit id>``.
    """
    rev = safe_rev(req.one("rev"))
    if req.one("since"):
        return _review_delta(ctx, req, rev)
    wanted = req.number("patchset")
    if wanted is not None:
        return commit_files(ctx, rev, patchset=wanted)
    branch = ctx.run.repo.working_branch
    [target] = resolve_show_targets(
        ctx.cwd, [rev], ctx.service.rest, settings=ctx.settings, branch=branch, changes=ctx.service.changes
    )
    return commit_files(ctx, rev, sha=target.row.sha, subject=target.row.subject, is_local=target.is_local_commit)


def commit_files(
    ctx: Context,
    changeish: str,
    *,
    sha: str | None = None,
    subject: str | None = None,
    is_local: bool = False,
    patchset: int | None = None,
) -> dict[str, Any]:
    """The answer of ``/diff`` without ``since``, and ``files`` of ``/show?files=1``.

    *sha* is the commit when it is known; one that is not here, or *patchset*, is fetched
    as the patchset of the change *changeish* names.
    """
    if not sha or commit_sha_or_none(ctx.cwd, sha) is None:
        found, _url = resolve_patchset(ctx.service, ctx.cwd, changeish, patchset)
        if commit_sha_or_none(ctx.cwd, found.commit) is None:
            fetch_patchset(ctx.cwd, ctx.settings.gerrit_remote, found)
        sha, subject = found.commit, subject or found.subject
    shown = show_commit_diff(ctx.cwd, sha)
    return {
        "commit": shown.diff.commit,
        "base": shown.diff.base,
        "subject": subject,
        "message": shown.meta.body,
        "author": shown.meta.author_line,
        "local_commit": is_local,
        "files": [asdict(change) for change in shown.diff.files],
    }


def patchsets(ctx: Context, req: Request) -> dict[str, Any]:
    """The patchsets Gerrit has of the change ``rev`` names, oldest first, and which you last reviewed."""
    change = resolve_change(ctx.service, ctx.cwd, safe_rev(req.one("rev")))
    view = change.view
    reviewed = last_reviewed_patch_set(ctx.service.rest.get_messages(change.key), ctx.service.self_account_id())
    known = sorted(
        (revision.number, sha, revision) for sha, revision in view.revisions.items() if revision.number is not None
    )
    return {
        "number": view.number,
        "current": view.current_revision_number,
        "reviewed": reviewed,
        "patchsets": [
            {"number": number, "commit": sha, "created": iso_utc(revision.created)} for number, sha, revision in known
        ],
    }


def file(ctx: Context, req: Request) -> dict[str, Any]:
    """Text of ``path`` at commit ``rev`` (an object id from ``/diff``); ``content`` is ``null`` when absent."""
    rev, path = req.one("rev") or "", req.one("path") or ""
    if not OBJECT_ID.fullmatch(rev):
        raise BadRequest("rev must be a commit id, as /diff returns it")
    if not path or path.startswith(("-", "/")) or "\0" in path:
        raise BadRequest("path is missing or not a repository path")
    content = None if rev == EMPTY_TREE else file_text(ctx.cwd, rev, path)
    return {"rev": rev, "path": path, "content": content}
