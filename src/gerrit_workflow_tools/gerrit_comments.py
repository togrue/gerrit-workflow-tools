from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from gerrit_workflow_tools.change_id import CHANGE_ID_VALUE_RE
from gerrit_workflow_tools.gerrit_client import (
    GerritApiError,
    GerritClient,
    pick_change_from_query_result,
    resolve_change_ref,
)
from gerrit_workflow_tools.git_run import GitError, git_out
from gerrit_workflow_tools.stack import (
    StackSnapshot,
    get_stack_snapshot,
    parse_change_id,
)

logger = logging.getLogger(__name__)


def _is_fixup_or_squash_subject(subject: str) -> bool:
    return subject.startswith("fixup!") or subject.startswith("squash!")


def _rev_list_newest_first(
    cwd: Path | str | None, merge_base: str, head: str
) -> list[str]:
    out = git_out("rev-list", f"{merge_base}..{head}", cwd=cwd)
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def select_commit_for_comments(
    cwd: Path | str | None,
    *,
    explicit_rev: str | None,
    skip_fixups: bool,
    branch: str | None = None,
    snapshot: StackSnapshot | None = None,
) -> str:
    """Pick the commit whose comments to show (newest on stack, optionally skipping fixup!/squash!)."""
    snap = snapshot or get_stack_snapshot(cwd, branch)
    mb = snap.merge_base
    by_sha = {r[0]: r[2] for r in snap.rows}

    if explicit_rev:
        head_sha = git_out("rev-parse", explicit_rev, cwd=cwd)
        shas = _rev_list_newest_first(cwd, mb, head_sha)
    else:
        head_sha = git_out("rev-parse", "HEAD", cwd=cwd)
        shas = [r[0] for r in reversed(snap.rows)]
        if snap.rows and snap.rows[-1][0] != head_sha:
            shas = _rev_list_newest_first(cwd, mb, head_sha)

    if not shas:
        raise GitError("no commits in local stack for gcomments (empty range)")
    logger.debug("candidate SHAs (newest first): %s", shas)
    if not skip_fixups:
        logger.info("select_commit: picked %s (skip_fixups=False)", shas[0][:12])
        return shas[0]
    for sha in shas:
        sub = by_sha.get(sha)
        if sub is None:
            sub = git_out("log", "-1", "--format=%s", sha, cwd=cwd)
        if not _is_fixup_or_squash_subject(sub):
            logger.info("select_commit: picked %s %r (skipped fixup/squash commits)", sha[:12], sub)
            return sha
    raise GitError("no non-fixup/squash commit found in stack (try --no-skip-fixups)")


def change_id_for_sha(
    cwd: Path | str | None, sha: str, *, raw_message: str | None = None
) -> str:
    """Return the validated Change-Id from *sha*'s message (or from *raw_message* if provided)."""
    raw = raw_message if raw_message is not None else git_out("log", "-1", "--format=%B", sha, cwd=cwd)
    cid = parse_change_id(raw)
    if not cid or not CHANGE_ID_VALUE_RE.match(cid.strip()):
        raise GitError(f"no valid Change-Id in commit {sha[:8]}")
    cid = cid.strip()
    logger.info("change_id_for_sha %s -> %s", sha[:12], cid)
    return cid


def resolve_change_for_gcomments(
    client: GerritClient,
    *,
    change_arg: str | None,
    local_change_id: str | None,
) -> dict[str, Any]:
    """Resolve ``--change`` or *local_change_id* to a single Gerrit change dict."""
    if change_arg:
        q = resolve_change_ref(change_arg)
        rows = client.query_changes(q, n=10)
        ch = pick_change_from_query_result(rows)
    elif local_change_id:
        rows = client.query_changes(f"change:{local_change_id}", n=10)
        ch = pick_change_from_query_result(rows)
    else:
        raise GitError("internal: no change specified")
    logger.info(
        "resolved change -> #%s %r (id=%s)",
        ch.get("_number"),
        ch.get("subject"),
        ch.get("id"),
    )
    logger.debug("resolved change detail: %s", ch)
    return ch


def ordered_relation_chain(
    client: GerritClient,
    first: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return *first* plus related changes, ordered by change number (dependency chain)."""
    cid = first.get("id")
    if not isinstance(cid, str) or not cid:
        raise GerritApiError("change has no id")
    related = client.get_related(cid, revision_id="current")
    if not related:
        return [first]
    merged: dict[str, dict[str, Any]] = {}
    for ch in related:
        i = ch.get("id")
        if isinstance(i, str) and i:
            merged[i] = ch
    if isinstance(cid, str) and cid and cid not in merged:
        merged[cid] = first
    ordered = sorted(
        merged.values(),
        key=lambda c: c.get("_number") if isinstance(c.get("_number"), int) else 0,
    )
    logger.info(
        "relation chain: %d change(s): %s",
        len(ordered),
        ", ".join(f"#{c.get('_number')} {c.get('subject', '')!r}" for c in ordered),
    )
    return ordered


@dataclass
class FlatComment:
    path: str | None
    line: int | None
    side: str | None
    unresolved: bool | None
    patch_set: int | None
    author: str
    updated: str
    message: str
    url: str
    comment_id: str | None
    change_number: int | None
    project: str | None


def _author_display(a: dict[str, Any] | None) -> str:
    if not a:
        return ""
    name = a.get("name")
    if isinstance(name, str) and name:
        return name
    aid = a.get("_account_id")
    return str(aid) if aid is not None else ""


def _comment_timestamp(c: dict[str, Any]) -> str:
    for k in ("updated", "date"):
        v = c.get(k)
        if isinstance(v, str) and v:
            return v
    return ""


def _comment_line(c: dict[str, Any]) -> int | None:
    ln = c.get("line")
    if isinstance(ln, int):
        return ln
    r = c.get("range")
    if isinstance(r, dict):
        sl = r.get("start_line")
        if isinstance(sl, int):
            return sl
    return None


def _comment_side(c: dict[str, Any]) -> str | None:
    s = c.get("side")
    if isinstance(s, str):
        return s
    return None


def _should_include_comment(
    c: dict[str, Any],
    *,
    strict_open: bool,
    include_all: bool,
) -> bool:
    u = c.get("unresolved")
    if include_all:
        return True
    if strict_open:
        return u is True
    # default: exclude only explicitly resolved
    return u is not False


def _path_for_comment_key(key: str) -> str | None:
    if "PATCHSET_LEVEL" in key or "COMMIT_MSG" in key:
        return None
    return key or None


def flatten_change_comments(
    web_base: str,
    change: dict[str, Any],
    file_map: dict[str, list[dict[str, Any]]],
    *,
    include_all: bool,
    strict_open: bool,
) -> list[FlatComment]:
    """Normalize Gerrit comment API output into sorted :class:`FlatComment` rows with thread URLs."""
    proj = change.get("project")
    project_str = proj if isinstance(proj, str) else None
    num = change.get("_number")
    cn = num if isinstance(num, int) else None
    out: list[FlatComment] = []

    for key, items in file_map.items():
        p = _path_for_comment_key(key)
        for c in items:
            if not _should_include_comment(
                c, strict_open=strict_open, include_all=include_all
            ):
                continue
            cid = c.get("id")
            cid_s = cid if isinstance(cid, str) else None
            link = comment_thread_url(web_base, project_str, cn, cid_s)
            c_unresolved = c.get("unresolved")
            c_patch_set = c.get("patch_set")
            c_author = c.get("author")
            c_message = c.get("message")
            out.append(
                FlatComment(
                    path=p,
                    line=_comment_line(c),
                    side=_comment_side(c),
                    unresolved=c_unresolved if isinstance(c_unresolved, bool) else None,
                    patch_set=c_patch_set if isinstance(c_patch_set, int) else None,
                    author=_author_display(c_author if isinstance(c_author, dict) else None),
                    updated=_comment_timestamp(c),
                    message=c_message if isinstance(c_message, str) else "",
                    url=link,
                    comment_id=cid_s,
                    change_number=cn,
                    project=project_str,
                )
            )

    def sort_key(fc: FlatComment) -> tuple[str, int, int, str]:
        pl = fc.path or ""
        ln = fc.line if fc.line is not None else 0
        ps = fc.patch_set if fc.patch_set is not None else 0
        return (pl, ln, ps, fc.updated)

    result = sorted(out, key=sort_key)
    logger.info(
        "flatten_change_comments #%s -> %d comment(s) (include_all=%s, strict_open=%s)",
        cn,
        len(result),
        include_all,
        strict_open,
    )
    logger.debug(
        "flattened comments: %s",
        [
            {"path": fc.path, "line": fc.line, "author": fc.author, "message": fc.message[:80]}
            for fc in result
        ],
    )
    return result


def comment_thread_url(
    web_base: str,
    project: str | None,
    change_number: int | None,
    comment_id: str | None,
) -> str:
    """Build the Gerrit web URL for a comment thread, or *web_base* if data is incomplete."""
    base = web_base.rstrip("/")
    if not project or change_number is None or not comment_id:
        return base
    proj_seg = quote(project, safe="")
    return f"{base}/c/{proj_seg}/+/{change_number}/comment/{comment_id}/"


def commit_display(cwd: Path | str | None, sha: str) -> tuple[str, str, str]:
    """Return ``(sha, subject, full_message_body)`` for *sha*."""
    combined = git_out("log", "-1", "--format=%s%x1e%B", sha, cwd=cwd)
    if "\x1e" in combined:
        sub, body = combined.split("\x1e", 1)
    else:
        sub, body = combined, ""
    return sha, sub, body


def local_change_map_from_stack(
    cwd: Path | str | None,
    *,
    snapshot: StackSnapshot | None = None,
) -> dict[str, tuple[str, str, str]]:
    """Map Change-Id → ``(full_sha, subject, message_body)`` for commits on the current stack."""
    snap = snapshot or get_stack_snapshot(cwd)
    rows = snap.rows
    out: dict[str, tuple[str, str, str]] = {}
    for sha, _short, subj, raw in rows:
        cid = parse_change_id(raw)
        if not cid:
            continue
        out[cid.strip()] = (sha, subj, raw)
    return out


def _resolve_commit_display(
    ch: dict[str, Any],
    local: dict[str, tuple[str, str, str]],
) -> tuple[str | None, str | None, str | None]:
    """Return (sha, subject, body), preferring local git data over Gerrit API data."""
    cid = ch.get("change_id")
    if isinstance(cid, str) and cid and cid in local:
        return local[cid]
    rev = ch.get("current_revision")
    sha: str | None = rev[:40] if isinstance(rev, str) and len(rev) >= 40 else (rev if isinstance(rev, str) else None)
    subj = ch.get("subject")
    return sha, (subj if isinstance(subj, str) else None), None


def build_human_display_payload(
    chain: list[dict[str, Any]],
    comments_by_change: list[list[FlatComment]],
    *,
    local_commit_by_change_id: dict[str, tuple[str, str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Build a list of per-change dicts (commit info + comments) for text rendering."""
    local = local_commit_by_change_id or {}
    out: list[dict[str, Any]] = []
    for ch, flats in zip(chain, comments_by_change):
        sha, subj, body = _resolve_commit_display(ch, local)
        commit_block: dict[str, Any] = {"sha": sha, "subject": subj, "body": body}
        comments: list[dict[str, Any]] = [
            {
                "path": fc.path,
                "line": fc.line,
                "unresolved": fc.unresolved,
                "patchSet": fc.patch_set,
                "author": fc.author,
                "updated": fc.updated,
                "message": fc.message,
                "url": fc.url,
            }
            for fc in flats
        ]
        out.append({"commit": commit_block, "comments": comments})
    return out


def format_human(
    changes_payload: list[dict[str, Any]],
    *,
    full: bool,
    oneline: bool,
) -> str:
    """Format *changes_payload* as human-readable text (full message and/or one line per comment)."""
    lines: list[str] = []
    for ch in changes_payload:
        raw_commit = ch.get("commit")
        commit: dict[str, Any] = raw_commit if isinstance(raw_commit, dict) else {}
        sha = commit.get("sha")
        subj = commit.get("subject")
        body = commit.get("body")
        if sha:
            lines.append(f"commit {sha}")
            lines.append("")
        # Full message from git %B includes the subject as the first line; do not
        # print subject again above it (avoids duplicate title with --full).
        if full and body and isinstance(body, str) and body.strip():
            for bline in body.strip().splitlines():
                lines.append(f"  {bline}")
            lines.append("")
        else:
            if subj:
                lines.append(f"  {subj}")
                lines.append("")
        valid_comments = [c for c in (ch.get("comments") or []) if isinstance(c, dict)]
        if not valid_comments:
            lines.append("  No comments")
            lines.append("")
        else:
            for c in valid_comments:
                path = c.get("path")
                line = c.get("line")
                loc = ""
                if path and line is not None:
                    loc = f"{path}:{line}"
                elif path:
                    loc = str(path)
                else:
                    loc = "(change)"
                u = c.get("unresolved")
                st = "Unresolved" if u is not False else "Resolved"
                msg = c.get("message") or ""
                first = msg.strip().splitlines()[0] if msg.strip() else ""
                if not full and len(first) > 160:
                    first = first[:157] + "..."
                link = c.get("url") or ""
                if oneline:
                    lines.append(f"{loc}  [{st}]  {first}  {link}".rstrip())
                    continue
                lines.append(f"  {loc} - {st} Comment")
                if link:
                    lines.append(f"  Link: {link}")
                auth = c.get("author") or ""
                ps = c.get("patchSet")
                ps_txt = f"Patch set {ps}" if ps is not None else ""
                meta_bits = [b for b in (auth, ps_txt, c.get("updated") or "") if b]
                lines.append(f"    {' -- '.join(meta_bits)}")
                msg_out = msg if full else (first if first else msg[:200])
                if msg_out:
                    for ml in msg_out.splitlines():
                        lines.append(f"      {ml}")
                lines.append("")
        lines.append("")
    return "\n".join(lines).rstrip() + ("\n" if lines else "")


def build_json_payload(
    chain: list[dict[str, Any]],
    comments_by_change: list[list[FlatComment]],
    *,
    local_commit_by_change_id: dict[str, tuple[str, str, str]] | None = None,
) -> dict[str, Any]:
    """Build the ``git gcomments --json`` structure: changes with commit metadata and comments."""
    local = local_commit_by_change_id or {}
    out_changes: list[dict[str, Any]] = []
    for ch, flats in zip(chain, comments_by_change):
        raw_cid = ch.get("change_id")
        cid = raw_cid if isinstance(raw_cid, str) else None
        raw_num = ch.get("_number")
        num = raw_num if isinstance(raw_num, int) else None
        raw_proj = ch.get("project")
        proj = raw_proj if isinstance(raw_proj, str) else None
        sha_txt, sub_txt, body_txt = _resolve_commit_display(ch, local)
        comments_json: list[dict[str, Any]] = [
            {
                "path": fc.path,
                "line": fc.line,
                "side": fc.side,
                "unresolved": fc.unresolved,
                "patchSet": fc.patch_set,
                "author": fc.author,
                "updated": fc.updated,
                "message": fc.message,
                "url": fc.url,
                "id": fc.comment_id,
            }
            for fc in flats
        ]
        out_changes.append(
            {
                "changeId": cid,
                "changeNumber": num,
                "project": proj,
                "commit": {
                    "sha": sha_txt,
                    "subject": sub_txt,
                    "body": body_txt,
                },
                "comments": comments_json,
            }
        )
    return {"changes": out_changes}
