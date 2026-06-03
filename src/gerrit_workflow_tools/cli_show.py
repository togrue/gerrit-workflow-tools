"""CLI for ``ger show``: one commit vs Gerrit (status + unresolved comments)."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from gerrit_workflow_tools.cli_common import (
    HELP_JSON,
    add_color_args,
    add_verbose_and_debug_log_args,
    init_cli_runtime,
)
from gerrit_workflow_tools.cli_style import (
    ANSI_CYAN,
    ANSI_DIM,
    ANSI_YELLOW,
    color_text,
)
from gerrit_workflow_tools.core.config import gshow_comment_tail_lines
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.gerrit_change_status import (
    collect_unresolved_comments,
    determine_attention,
    gerrit_inline_comment_url,
)
from gerrit_workflow_tools.core.gerrit_client import GerritApiError
from gerrit_workflow_tools.core.gerrit_show import resolve_show_commit_row
from gerrit_workflow_tools.core.git_run import GitError, git_out
from gerrit_workflow_tools.render.commit_row import attention_column, extra_detail_lines, oneline_body

logger = logging.getLogger(__name__)

_EXIT_ATTENTION = 1
_EXIT_ERROR = 2


def _apply_comment_tail(text: str, tail_lines: int, *, full: bool) -> tuple[str, bool]:
    if full:
        return text, False
    lines = text.splitlines()
    if len(lines) <= tail_lines:
        return text, False
    omitted = len(lines) - tail_lines
    body = "\n".join(lines[-tail_lines:])
    return f"[... {omitted} lines omitted above]\n{body}", True


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the command-line parser for ``ger show``."""
    p = argparse.ArgumentParser(
        prog="ger show",
        description="Show one commit and its Gerrit status (labels, comments, CI).",
    )
    p.add_argument(
        "rev",
        nargs="?",
        default=None,
        metavar="REV",
        help="Git ref, Change-Id, change number, or Gerrit query (default: HEAD).",
    )
    p.add_argument(
        "--full",
        action="store_true",
        help="Show full comment bodies without tail truncation.",
    )
    p.add_argument(
        "--comment-tail-lines",
        type=int,
        metavar="LINES",
        default=None,
        help=("Show only the last N lines of each comment body (positive integer; overrides config)."),
    )
    p.add_argument(
        "--json",
        action="store_true",
        dest="json_",
        help=HELP_JSON,
    )
    add_color_args(p)
    add_verbose_and_debug_log_args(
        p,
        debug_log_help="Log Gerrit resolution to stderr.",
    )
    return p


def main(argv: list[str] | None = None) -> int:  # pylint: disable=too-many-return-statements,too-many-branches,too-many-locals,too-many-statements
    """Resolve one revision and print human-readable or JSON Gerrit status details."""
    p = _build_parser()
    args = p.parse_args(argv)
    cwd, summary_highlighter = init_cli_runtime(debug_log=args.debug_log, color=args.color)

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
        service = GerritService.from_cwd(cwd)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return _EXIT_ERROR

    try:
        resolved = resolve_show_commit_row(cwd, args.rev, service.rest)
    except GerritApiError as e:
        print(f"error: {e}", file=sys.stderr)
        return _EXIT_ERROR
    except GitError as e:
        print(f"error: {e}", file=sys.stderr)
        return _EXIT_ERROR

    row = resolved.row
    is_local = resolved.is_local_commit
    try:
        commits = service.fetch_gerrit_data([row], cwd=cwd)
    except GerritApiError as e:
        print(f"gerrit error: {e}", file=sys.stderr)
        return _EXIT_ERROR

    if not commits:
        print("error: no commit data", file=sys.stderr)
        return _EXIT_ERROR
    commit = commits[0]
    attention = determine_attention(commit, chain_blocked=False)
    cid = commit.change_id

    if commit.pushed:
        try:
            file_map = service.comments.get_file_map(cid)
        except GerritApiError as e:
            print(f"gerrit error: {e}", file=sys.stderr)
            return _EXIT_ERROR
    else:
        file_map = {}

    unresolved_rows = collect_unresolved_comments(file_map)

    if args.json_:
        # Human output honors --comment-tail-lines / --full; JSON always emits full text.
        comment_payload: list[dict[str, object]] = []
        for row_item in unresolved_rows:
            entry: dict[str, object] = {
                "path": row_item.path,
                "line": row_item.line,
                "message": row_item.message,
                "url": gerrit_inline_comment_url(commit.gerrit_url, row_item.comment_id),
            }
            if row_item.author:
                entry["author"] = row_item.author
            comment_payload.append(entry)
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
            "change_status": commit.change_status,
            "merged_equivalent": commit.merged_equivalent,
        }
        print(json.dumps(payload, indent=2))
        return _EXIT_ATTENTION if attention else 0

    if is_local and commit.sha:
        try:
            msg = git_out("show", "-s", "--no-patch", "--pretty=medium", commit.sha, cwd=cwd)
        except GitError as e:
            print(f"error: {e}", file=sys.stderr)
            return _EXIT_ERROR
        print()
        print(msg.rstrip())

    ind = " " * 4
    print()
    if commit.gerrit_url:
        print(f"{ind}{color_text(commit.gerrit_url, ANSI_DIM)}")
    for d in extra_detail_lines(commit):
        print(f"{ind}{d}")
    attn_col = attention_column([commit], summary_highlighter=summary_highlighter)
    print(f"{ind}{oneline_body(commit, summary_highlighter=summary_highlighter, attention_col=attn_col)}")

    print()
    print(color_text("Unresolved comments:", ANSI_YELLOW))
    if not commit.pushed:
        print("  (not on Gerrit — no comments)")
    elif unresolved_rows:
        for row_item in unresolved_rows:
            body, _trunc = _apply_comment_tail(row_item.message, tail_n, full=args.full)
            comment_url = gerrit_inline_comment_url(commit.gerrit_url, row_item.comment_id) or commit.gerrit_url
            loc = f"{row_item.path}:{row_item.line}" if row_item.line is not None else row_item.path
            loc_line = f"  {color_text(loc, ANSI_CYAN)}"
            if row_item.author:
                loc_line += f"  {color_text(row_item.author, ANSI_DIM)}"
            print(loc_line)
            if comment_url:
                print(f"  {color_text('url:', ANSI_DIM)} {color_text(comment_url, ANSI_YELLOW)}")
            for ln in body.splitlines():
                print(f"    {ln}")
            print()
    else:
        print("  (no unresolved comments)")

    return _EXIT_ATTENTION if attention else 0


if __name__ == "__main__":
    raise SystemExit(main())
