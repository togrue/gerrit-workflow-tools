"""CLI for ``git gshow``: one commit vs Gerrit (status + unresolved comments)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from gerrit_workflow_tools.change_id import CHANGE_ID_VALUE_RE, is_change_id_token
from gerrit_workflow_tools.cli_common import HELP_JSON, configure_logging, cwd_from_env
from gerrit_workflow_tools.cli_gcid import resolve_gcid_user_arg
from gerrit_workflow_tools.cli_glog import _detail_lines, _primary_line, _url_line
from gerrit_workflow_tools.config import gshow_comment_tail_lines
from gerrit_workflow_tools.gerrit_change_status import determine_attention, fetch_gerrit_data
from gerrit_workflow_tools.gerrit_client import GerritApiError, GerritClient
from gerrit_workflow_tools.gerrit_comments import resolve_change_for_gcomments
from gerrit_workflow_tools.gerrit_url import resolve_gerrit_web_base
from gerrit_workflow_tools.git_run import GitError, git_out
from gerrit_workflow_tools.stack import parse_change_id

logger = logging.getLogger(__name__)

_EXIT_ATTENTION = 1
_EXIT_ERROR = 2


def _arg_has_range(s: str) -> bool:
    t = s.strip()
    return ".." in t or "..." in t


def _looks_like_change_id(s: str) -> bool:
    t = s.strip()
    if is_change_id_token(t):
        return True
    return bool(CHANGE_ID_VALUE_RE.match(t))


def _is_numeric_change(s: str) -> bool:
    t = s.strip()
    return bool(t) and t.isdigit()


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


def _apply_comment_tail(text: str, tail_lines: int, *, full: bool) -> tuple[str, bool]:
    if full:
        return text, False
    lines = text.splitlines()
    if len(lines) <= tail_lines:
        return text, False
    omitted = len(lines) - tail_lines
    body = "\n".join(lines[-tail_lines:])
    return f"[... {omitted} lines omitted above]\n{body}", True


def _collect_unresolved_comments(
    file_map: dict[str, list[dict[str, Any]]],
) -> list[tuple[str, int | None, dict[str, Any]]]:
    out: list[tuple[str, int | None, dict[str, Any]]] = []
    for path, clist in file_map.items():
        for c in clist:
            if isinstance(c, dict) and c.get("unresolved") is True:
                out.append((path, _comment_line(c), c))
    out.sort(key=lambda x: (x[0], x[1] if x[1] is not None else -1))
    return out


def resolve_row_for_gshow(
    cwd: Path | str,
    arg: str | None,
    client: GerritClient,
) -> tuple[tuple[str, str, str, str | None], bool]:
    """Return ``(sha, short, summary, change_id), is_local_git`` for :func:`fetch_gerrit_data`."""
    a = (arg or "HEAD").strip()
    if _arg_has_range(a):
        raise GitError(f"gshow does not support revision ranges: {arg!r}")

    if _looks_like_change_id(a) or _is_numeric_change(a):
        ch = resolve_change_for_gcomments(client, change_arg=a, local_change_id=None)
        rev = ch.get("current_revision")
        sha = rev if isinstance(rev, str) else ""
        chg_id = ch.get("change_id")
        if not isinstance(chg_id, str):
            raise GitError("Gerrit change has no change_id")
        subj = ch.get("subject")
        summary = subj if isinstance(subj, str) else ""
        short = sha[:8] if len(sha) >= 8 else "?" * min(8, max(1, len(sha) or 1))
        if not sha:
            short = "????????"
        return (sha, short, summary, chg_id), False

    try:
        resolved = resolve_gcid_user_arg(cwd, a)
    except GitError:
        ch = resolve_change_for_gcomments(client, change_arg=a, local_change_id=None)
        rev = ch.get("current_revision")
        sha = rev if isinstance(rev, str) else ""
        chg_id = ch.get("change_id")
        if not isinstance(chg_id, str):
            raise GitError("Gerrit change has no change_id") from None
        subj = ch.get("subject")
        summary = subj if isinstance(subj, str) else ""
        short = sha[:8] if len(sha) >= 8 else "????????"
        if not sha:
            short = "????????"
        return (sha, short, summary, chg_id), False

    if ".." in resolved or "..." in resolved:
        raise GitError(f"gshow does not support revision ranges: {arg!r}")

    sha = git_out("rev-parse", "--verify", resolved, cwd=cwd)
    raw = git_out("log", "-1", "--format=%B", sha, cwd=cwd)
    summary = git_out("log", "-1", "--format=%s", sha, cwd=cwd)
    short = git_out("log", "-1", "--format=%h", sha, cwd=cwd)
    cid = parse_change_id(raw)
    if not cid:
        raise GitError(f"no Change-Id in commit {short}")
    return (sha, short, summary, cid), True


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="git gshow",
        description="Show one commit and its Gerrit status (labels, comments, CI).",
    )
    p.add_argument(
        "rev",
        nargs="?",
        default=None,
        metavar="REV",
        help="Git ref, Change-Id, change number, or Gerrit query (default: HEAD)",
    )
    p.add_argument(
        "--full",
        action="store_true",
        help="show full comment bodies (no tail truncation)",
    )
    p.add_argument(
        "--comment-tail-lines",
        type=int,
        metavar="N",
        default=None,
        help="show only the last N lines of each comment body (positive integer; overrides config)",
    )
    p.add_argument(
        "--json",
        action="store_true",
        dest="json_",
        help=HELP_JSON,
    )
    p.add_argument("--no-color", action="store_true", help="disable colored output")
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="log Gerrit resolution to stderr",
    )
    args = p.parse_args(argv)
    configure_logging(1 if args.verbose else 0)
    cwd = cwd_from_env()
    use_color = not args.no_color and sys.stdout.isatty()

    if args.comment_tail_lines is not None and args.comment_tail_lines < 1:
        print(
            "error: --comment-tail-lines must be a positive integer",
            file=sys.stderr,
        )
        return _EXIT_ERROR

    tail_n = args.comment_tail_lines
    if tail_n is None:
        tail_n = gshow_comment_tail_lines(cwd)

    try:
        web_base = resolve_gerrit_web_base(cwd)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return _EXIT_ERROR

    client = GerritClient(web_base, cwd=str(cwd))

    try:
        row, is_local = resolve_row_for_gshow(cwd, args.rev, client)
    except GerritApiError as e:
        print(f"error: {e}", file=sys.stderr)
        return _EXIT_ERROR
    except GitError as e:
        print(f"error: {e}", file=sys.stderr)
        return _EXIT_ERROR

    try:
        commits = fetch_gerrit_data(client, web_base, [row])
    except GerritApiError as e:
        print(f"gerrit error: {e}", file=sys.stderr)
        return _EXIT_ERROR

    if not commits:
        print("error: no commit data", file=sys.stderr)
        return _EXIT_ERROR
    commit = commits[0]
    attention = determine_attention(commit, chain_blocked=False)

    cid = commit.change_id
    if not cid:
        print("error: missing Change-Id", file=sys.stderr)
        return _EXIT_ERROR

    try:
        file_map = client.get_comments(cid)
    except GerritApiError as e:
        print(f"gerrit error: {e}", file=sys.stderr)
        return _EXIT_ERROR

    unresolved_rows = _collect_unresolved_comments(file_map)

    if args.json_:
        comment_payload: list[dict[str, Any]] = []
        for path, line, c in unresolved_rows:
            raw_msg = c.get("message")
            msg = raw_msg if isinstance(raw_msg, str) else ""
            body, truncated = _apply_comment_tail(msg, tail_n, full=args.full)
            comment_payload.append(
                {
                    "path": path,
                    "line": line,
                    "body": body,
                    "truncated": truncated,
                }
            )
        payload = {
            "sha": commit.sha if commit.sha else None,
            "change_id": cid,
            "summary": commit.summary,
            "pushed": commit.pushed,
            "patchset_status": commit.patchset_status,
            "verified": commit.verified,
            "code_review": commit.code_review,
            "comments_unresolved": commit.comments_unresolved,
            "ci_failures": commit.ci_failures,
            "gerrit_url": commit.gerrit_url,
            "submittable": commit.submittable,
            "attention_reasons": attention,
            "comments": comment_payload,
            "local_commit": is_local,
        }
        print(json.dumps(payload, indent=2))
        return _EXIT_ATTENTION if attention else 0

    # Human output
    if commit.sha:
        print(f"commit: {commit.short_sha}  {commit.sha}")
    else:
        print("commit: (no local revision)")
    print(f"subject: {commit.summary}")

    if is_local and commit.sha:
        try:
            msg = git_out("show", "-s", "--no-patch", "--pretty=medium", commit.sha, cwd=cwd)
        except GitError as e:
            print(f"error: {e}", file=sys.stderr)
            return _EXIT_ERROR
        print()
        print(msg.rstrip())

    print()
    print(_primary_line(commit, use_color=use_color))
    ind = " " * 8
    if commit.gerrit_url:
        print(f"{ind}{_url_line(commit.gerrit_url, use_color=use_color)}")
    for d in _detail_lines(commit, use_color=use_color):
        print(f"{ind}{d}")

    url = commit.gerrit_url or ""
    if unresolved_rows:
        print()
        print("unresolved comments:")
        for path, line, c in unresolved_rows:
            raw_msg = c.get("message")
            msg = raw_msg if isinstance(raw_msg, str) else ""
            body, _trunc = _apply_comment_tail(msg, tail_n, full=args.full)
            loc = f"{path}:{line}" if line is not None else path
            print(f"  {loc}")
            if url:
                print(f"  url: {url}")
            for ln in body.splitlines():
                print(f"  {ln}")
            print()

    return _EXIT_ATTENTION if attention else 0


if __name__ == "__main__":
    raise SystemExit(main())
