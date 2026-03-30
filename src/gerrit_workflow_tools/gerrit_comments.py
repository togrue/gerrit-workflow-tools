from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
    if not skip_fixups:
        return shas[0]
    for sha in shas:
        sub = by_sha.get(sha)
        if sub is None:
            sub = git_out("log", "-1", "--format=%s", sha, cwd=cwd)
        if not _is_fixup_or_squash_subject(sub):
            return sha
    raise GitError("no non-fixup/squash commit found in stack (try --no-skip-fixups)")


def change_id_for_sha(
    cwd: Path | str | None, sha: str, *, raw_message: str | None = None
) -> str:
    raw = raw_message if raw_message is not None else git_out("log", "-1", "--format=%B", sha, cwd=cwd)
    cid = parse_change_id(raw)
    if not cid or not CHANGE_ID_VALUE_RE.match(cid.strip()):
        raise GitError(f"no valid Change-Id in commit {sha[:8]}")
    return cid.strip()


def resolve_change_for_gcomments(
    client: GerritClient,
    *,
    change_arg: str | None,
    local_change_id: str | None,
) -> dict[str, Any]:
    if change_arg:
        q = resolve_change_ref(change_arg)
        rows = client.query_changes(q, n=10)
        return pick_change_from_query_result(rows)
    if local_change_id:
        rows = client.query_changes(f"change:{local_change_id}", n=10)
        return pick_change_from_query_result(rows)
    raise GitError("internal: no change specified")


def ordered_relation_chain(
    client: GerritClient,
    first: dict[str, Any],
) -> list[dict[str, Any]]:
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
    fid = first.get("id")
    if isinstance(fid, str) and fid and fid not in merged:
        merged[fid] = first
    ordered = sorted(
        merged.values(),
        key=lambda c: c.get("_number") if isinstance(c.get("_number"), int) else 0,
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


def flatten_change_comments(
    web_base: str,
    change: dict[str, Any],
    file_map: dict[str, list[dict[str, Any]]],
    *,
    include_all: bool,
    strict_open: bool,
) -> list[FlatComment]:
    proj = change.get("project")
    project_str = proj if isinstance(proj, str) else None
    num = change.get("_number")
    cn = num if isinstance(num, int) else None
    out: list[FlatComment] = []

    def path_for_key(key: str) -> str | None:
        if "PATCHSET_LEVEL" in key or "COMMIT_MSG" in key:
            return None
        return key or None

    for key, items in file_map.items():
        p = path_for_key(key)
        for c in items:
            if not _should_include_comment(
                c, strict_open=strict_open, include_all=include_all
            ):
                continue
            cid = c.get("id")
            cid_s = cid if isinstance(cid, str) else None
            link = comment_thread_url(web_base, project_str, cn, cid_s)
            out.append(
                FlatComment(
                    path=p,
                    line=_comment_line(c),
                    side=_comment_side(c),
                    unresolved=c.get("unresolved")
                    if isinstance(c.get("unresolved"), bool)
                    else None,
                    patch_set=c.get("patch_set")
                    if isinstance(c.get("patch_set"), int)
                    else None,
                    author=_author_display(
                        c.get("author") if isinstance(c.get("author"), dict) else None
                    ),
                    updated=_comment_timestamp(c),
                    message=c.get("message")
                    if isinstance(c.get("message"), str)
                    else "",
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

    return sorted(out, key=sort_key)


def comment_thread_url(
    web_base: str,
    project: str | None,
    change_number: int | None,
    comment_id: str | None,
) -> str:
    base = web_base.rstrip("/")
    if not project or change_number is None or not comment_id:
        return base
    from urllib.parse import quote

    proj_seg = quote(project, safe="")
    return f"{base}/c/{proj_seg}/+/{change_number}/comment/{comment_id}/"


def commit_display(cwd: Path | str | None, sha: str) -> tuple[str, str, str]:
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
    """Map Change-Id -> (full_sha, subject, full_message body) for merge_base..HEAD."""
    snap = snapshot or get_stack_snapshot(cwd)
    rows = snap.rows
    out: dict[str, tuple[str, str, str]] = {}
    for sha, _short, subj, raw in rows:
        cid = parse_change_id(raw)
        if not cid:
            continue
        out[cid.strip()] = (sha, subj, raw)
    return out


def build_human_display_payload(
    chain: list[dict[str, Any]],
    comments_by_change: list[list[FlatComment]],
    *,
    local_commit_by_change_id: dict[str, tuple[str, str, str]] | None = None,
) -> list[dict[str, Any]]:
    local = local_commit_by_change_id or {}
    out: list[dict[str, Any]] = []
    for ch, flats in zip(chain, comments_by_change):
        cid = ch.get("change_id") if isinstance(ch.get("change_id"), str) else None
        commit_block: dict[str, Any] = {}
        if cid and cid in local:
            sha, sub, body = local[cid]
            commit_block = {"sha": sha, "subject": sub, "body": body}
        else:
            rev = (
                ch.get("current_revision")
                if isinstance(ch.get("current_revision"), str)
                else None
            )
            subj = ch.get("subject") if isinstance(ch.get("subject"), str) else None
            commit_block = {
                "sha": rev[:40] if rev and len(rev) >= 40 else rev,
                "subject": subj,
                "body": None,
            }
        comments: list[dict[str, Any]] = []
        for fc in flats:
            comments.append(
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
            )
        out.append({"commit": commit_block, "comments": comments})
    return out


def format_human(
    changes_payload: list[dict[str, Any]],
    *,
    full: bool,
    oneline: bool,
) -> str:
    lines: list[str] = []
    for ch in changes_payload:
        commit = ch.get("commit") or {}
        sha = commit.get("sha") if isinstance(commit, dict) else None
        subj = commit.get("subject") if isinstance(commit, dict) else None
        body = commit.get("body") if isinstance(commit, dict) else None
        if sha:
            lines.append(f"commit {sha}")
            lines.append("")
        if subj:
            lines.append(f"  {subj}")
            lines.append("")
        if full and body and isinstance(body, str) and body.strip():
            for bline in body.strip().splitlines():
                lines.append(f"  {bline}")
            lines.append("")
        for c in ch.get("comments") or []:
            if not isinstance(c, dict):
                continue
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
    """local_commit_by_change_id maps Change-Id -> (full_sha, subject, body)."""
    local = local_commit_by_change_id or {}
    out_changes: list[dict[str, Any]] = []
    for ch, flats in zip(chain, comments_by_change):
        cid = ch.get("change_id") if isinstance(ch.get("change_id"), str) else None
        num = ch.get("_number") if isinstance(ch.get("_number"), int) else None
        proj = ch.get("project") if isinstance(ch.get("project"), str) else None
        subj = ch.get("subject") if isinstance(ch.get("subject"), str) else None
        rev = (
            ch.get("current_revision")
            if isinstance(ch.get("current_revision"), str)
            else None
        )
        sha_txt: str | None = rev[:40] if rev and len(rev) >= 7 else rev
        sub_txt: str | None = subj
        body_txt: str | None = None
        if cid and cid in local:
            sha_txt, sub_txt, body_txt = local[cid]
        comments_json: list[dict[str, Any]] = []
        for fc in flats:
            comments_json.append(
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
            )
        out_changes.append(
            {
                "changeId": cid,
                "changeNumber": num,
                "project": proj,
                "subject": sub_txt,
                "commit": {
                    "sha": sha_txt,
                    "subject": sub_txt,
                    "body": body_txt,
                },
                "comments": comments_json,
            }
        )
    return {"changes": out_changes}
