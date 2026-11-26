"""Read endpoints of ``ger serve`` about what a change changed: its diff, its patchsets, a file's text.

Spec: docu/spec/commands/serve.md.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from gerrit_workflow_tools.core.anchor_mapping import WorktreeMap
from gerrit_workflow_tools.core.change_id import split_change_id_footer
from gerrit_workflow_tools.core.comment_chain import CommentChain
from gerrit_workflow_tools.core.commit_diff import EMPTY_TREE, file_text, show_commit_diff
from gerrit_workflow_tools.core.gerrit_patchset import fetch_patchset, resolve_change, resolve_patchset
from gerrit_workflow_tools.core.gerrit_show import resolve_show_targets
from gerrit_workflow_tools.core.git_run import commit_sha_or_none
from gerrit_workflow_tools.core.review_delta import last_reviewed_patch_set, load_review_delta
from gerrit_workflow_tools.core.reviewed_files import ReviewedPathError, mark_reviewed, reviewed_files
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
    message_text, change_id = split_change_id_footer(shown.meta.body)
    return {
        "commit": shown.diff.commit,
        "base": shown.diff.base,
        "subject": subject,
        "message": shown.meta.body,
        "message_text": message_text,
        "change_id": change_id,
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


def file(cwd: Path, req: Request) -> dict[str, Any]:
    """Text of ``path`` at commit ``rev`` (an object id from ``/diff``); ``content`` is ``null`` when absent.

    Reads one immutable object, nothing else: it runs beside the request being served (a checkout
    that moves the working tree cannot change what a commit id names).
    """
    rev, path = req.one("rev") or "", req.one("path") or ""
    if not OBJECT_ID.fullmatch(rev):
        raise BadRequest("rev must be a commit id, as /diff returns it")
    if not path or path.startswith(("-", "/")) or "\0" in path:
        raise BadRequest("path is missing or not a repository path")
    content = None if rev == EMPTY_TREE else file_text(cwd, rev, path)
    return {"rev": rev, "path": path, "content": content}


def reviewed(ctx: Context, req: Request) -> dict[str, Any]:
    """The files of patchset ``patchset`` of change ``change`` (numbers) you marked reviewed on Gerrit."""
    change, patch_set = _change_patchset(req.number("change"), req.number("patchset"))
    return {"change": change, "patch_set": patch_set, "reviewed": reviewed_files(ctx.service.rest, change, patch_set)}


def mark_reviewed_files(ctx: Context, req: Request) -> dict[str, Any]:
    """Mark ``path`` (or each of ``paths``) of that patchset reviewed on Gerrit (``reviewed`` true, the
    default), or clear the mark: one request here, one to Gerrit per path (it has no call for several)."""
    change, patch_set = _change_patchset(req.body_field("change", int), req.body_field("patchset", int))
    one, several = req.body_field("path", str), req.body_field("paths", list)
    if (one is None) == (several is None) or (several is not None and not all(isinstance(p, str) for p in several)):
        raise BadRequest("give path, a repository path, or paths, a list of them")
    flag = req.body_field("reviewed", bool) is not False
    try:
        marked = mark_reviewed(ctx.service.rest, change, patch_set, [one] if one is not None else several, flag)
    except ReviewedPathError as error:
        raise BadRequest(str(error)) from error
    answer: dict[str, Any] = {"change": change, "patch_set": patch_set, "reviewed": flag}
    return {**answer, "path": one} if one is not None else {**answer, "paths": marked}


def _change_patchset(change: int | None, patch_set: int | None) -> tuple[int, int]:
    """Both as Gerrit numbers them, or :class:`BadRequest`: no change is resolved, so it costs one request."""
    if change is None or change <= 0 or patch_set is None or patch_set <= 0:
        raise BadRequest("change and patchset are the change's number and a patchset number")
    return change, patch_set


def add_worktree_places(cwd: Path, served: list[dict[str, Any]], chains: Sequence[CommentChain]) -> None:
    """Where each open Gerrit chain of a local commit sits in the working files, ``worktree``; and
    for each replacement its comments suggest, its range there (``worktree``, while those lines are
    unchanged) and the text it replaces in the commented version (``original``). ``null`` where it
    cannot be told. Mapped from the version each comment was written on (``commit_id``)."""
    places = WorktreeMap(cwd)
    for payload, chain in zip(served, chains, strict=True):
        root = chain.comments[0] if chain.comments else None
        commit = root.commit_id if root and not chain.local and not chain.resolved and chain.line else None
        placed = places.place(commit, chain.path, chain.start_line or chain.line or 1, chain.line or 1)
        payload["worktree"] = asdict(placed) if placed else None
        for entry, comment in zip(payload["comments"], chain.comments, strict=True):
            for edit_json, edit in zip(entry.get("suggested_edits", []), comment.suggested_edits, strict=True):
                for rep_json, rep in zip(edit_json["replacements"], edit.replacements, strict=True):
                    at = comment.commit_id if commit else None
                    rng = places.range(at, rep.path, rep.range)
                    rep_json["worktree"] = rng.as_gerrit() if rng else None
                    rep_json["original"] = places.text(at, rep.path, rep.range)
