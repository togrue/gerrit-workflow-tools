"""CLI for stack-aware Gerrit status over local commits."""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

from gerrit_workflow_tools.cli_common import (
    HELP_JSON,
    add_color_args,
    add_follow_merges_args,
    add_verbose_and_debug_log_args,
    init_cli_runtime,
)
from gerrit_workflow_tools.cli_style import (
    ANSI_BOLD,
    ANSI_CYAN,
    ANSI_DIM,
    ANSI_GREEN,
    ANSI_RED,
    ANSI_YELLOW,
    color_text,
    visible_len,
)
from gerrit_workflow_tools.core.config import current_branch, log_defaults, resolve_working_branch
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.gerrit_change_status import (
    CommitStatusInput,
    LogCommit,
    annotate_attention,
)
from gerrit_workflow_tools.core.gerrit_client import GerritApiError
from gerrit_workflow_tools.core.git_run import GitError
from gerrit_workflow_tools.core.stack import commits_in_range
from gerrit_workflow_tools.core.upstream_interactive import branch_has_upstream, ensure_branch_upstream_interactive
from gerrit_workflow_tools.render.commit_row import (
    attention_column,
    continuation_indent,
    extra_detail_lines,
    oneline_body,
    oneline_line,
)
from gerrit_workflow_tools.summary_highlight import SummaryHighlighter

logger = logging.getLogger(__name__)
_UPSTREAM_TOKEN_RE = re.compile(r"(?P<branch>[^\s@]+)?@\{upstream\}")


def load_annotated_commits(
    cwd: Path,
    rev_range: str,
    *,
    first_parent: bool = False,
) -> tuple[list[LogCommit] | None, int]:
    """Load local commits in *rev_range*, enrich from Gerrit, and annotate attention."""
    commit_data, exit_code = _load_commits_in_range(cwd, rev_range, first_parent=first_parent)
    if commit_data is None:
        return None, exit_code
    commits, gerrit_exit = _fetch_enriched_commits(cwd, commit_data)
    if gerrit_exit is not None:
        return None, gerrit_exit
    assert commits is not None
    annotate_attention(commits)
    return commits, 0


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def _build_summary(commits: list[LogCommit]) -> tuple[dict[str, int], int, int]:
    """Return (per-category counts, ready count, total commits) for summary printing."""
    counts: dict[str, int] = {
        "ci-failures": 0,
        "unresolved-comments": 0,
        "on-gerrit": 0,
    }
    ready = 0
    total = len(commits)
    for c in commits:
        if c.patchset_status in ("absent", "newer", "merged-same"):
            ready += 1
        if c.pushed and c.verified is not None and c.verified <= -1:
            counts["ci-failures"] += 1
        if c.comments_unresolved > 0:
            counts["unresolved-comments"] += 1
        if c.pushed:
            counts["on-gerrit"] += 1
    return counts, ready, total


def _format_summary_dashboard_line(
    summary: dict[str, int],
    ready_n: int,
    total_n: int,
) -> str:
    """Single-line summary: ``summary: ready N/M · …`` with optional ANSI styling."""
    sep = " · "
    parts: list[str] = []

    label = "summary:"
    parts.append(color_text(label, f"{ANSI_BOLD}{ANSI_CYAN}"))
    parts.append(" ")
    parts.append(color_text("ready ", ANSI_DIM))
    parts.append(color_text(f"{ready_n}/{total_n}", ANSI_GREEN))

    ci = summary.get("ci-failures", 0)
    if ci:
        parts.append(color_text(sep, ANSI_DIM))
        parts.append(color_text("CI ", ANSI_DIM))
        parts.append(color_text(str(ci), ANSI_RED))

    unres = summary.get("unresolved-comments", 0)
    if unres:
        parts.append(color_text(sep, ANSI_DIM))
        parts.append(color_text("comments ", ANSI_DIM))
        parts.append(color_text(str(unres), ANSI_YELLOW))

    on_gerrit = summary.get("on-gerrit", 0)
    if on_gerrit:
        parts.append(color_text(sep, ANSI_DIM))
        parts.append(color_text("on-gerrit ", ANSI_DIM))
        parts.append(color_text(str(on_gerrit), ANSI_CYAN))

    return "".join(parts)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the command-line parser for ``ger log``."""
    parser = argparse.ArgumentParser(
        prog="ger log",
        description="Compact, actionable overview of the local commit chain vs Gerrit.",
    )
    parser.add_argument("--json", action="store_true", dest="json_", help=HELP_JSON)
    add_color_args(parser)
    parser.add_argument(
        "--url",
        "--show-url",
        action="store_true",
        dest="url",
        help=(
            "Include each change's Gerrit web URL in text output (JSON always includes gerrit_url). "
            "Default: ``gerrit.logShowUrl``."
        ),
    )
    parser.add_argument(
        "--show-change-id",
        action="store_true",
        help="Append Change-Id to each text line. Default: ``gerrit.logShowChangeId``.",
    )
    add_verbose_and_debug_log_args(
        parser,
        debug_log_help="Log git commands to stderr.",
        verbose_help=(
            "Expanded layout: oneline summary, indented details, Gerrit URL on the next line when URLs are on. "
            "Does not enable diagnostic logging; use ``--debug-log`` for that."
        ),
    )
    add_follow_merges_args(parser)
    parser.add_argument(
        "rev_range",
        nargs="?",
        default=None,
        metavar="REV_RANGE",
        help="Commit range (e.g. origin/main..HEAD); default <branch>@{upstream}..<branch>.",
    )
    return parser


def resolve_rev_range(cwd: Path, arg_rev_range: str | None) -> tuple[str | None, int | None]:
    """Return revision range or (None, exit_code) on error."""
    if arg_rev_range:
        if ".." in arg_rev_range:
            return arg_rev_range, None
        return f"{arg_rev_range}@{{upstream}}..{arg_rev_range}", None
    try:
        branch = resolve_working_branch(cwd) or current_branch(cwd)
    except GitError as e:
        print(f"error: {e}", file=sys.stderr)
        return None, 2
    if branch == "HEAD":
        return "@{upstream}..HEAD", None
    return f"{branch}@{{upstream}}..{branch}", None


def rev_range_needs_upstream_resolution(cwd: Path, rev_range: str) -> list[str]:
    """Return branch names that need upstream resolution for *rev_range*."""
    current = resolve_working_branch(cwd) or current_branch(cwd)
    required: list[str] = []
    seen: set[str] = set()
    for match in _UPSTREAM_TOKEN_RE.finditer(rev_range):
        branch = (match.group("branch") or current).lstrip(".")
        if branch in ("HEAD", ""):
            branch = current
        if branch in seen:
            continue
        seen.add(branch)
        required.append(branch)
    return required


def _load_commits_in_range(
    cwd: Path, rev_range: str, *, first_parent: bool = True
) -> tuple[list[CommitStatusInput] | None, int]:
    """Load local commits for Gerrit enrichment; return (commit_data, exit_code)."""
    try:
        rows = commits_in_range(cwd, rev_range, first_parent=first_parent)
    except GitError as e:
        print(f"error: {e}", file=sys.stderr)
        return None, 2
    if not rows:
        print("(no commits in range)")
        return None, 0
    commit_data = [
        CommitStatusInput(sha=c.sha, short_sha=c.short_sha, summary=c.subject, change_id=c.change_id) for c in rows
    ]
    return commit_data, 0


def _fetch_enriched_commits(
    cwd: Path,
    commit_data: list[CommitStatusInput],
) -> tuple[list[LogCommit] | None, int | None]:
    """Fetch Gerrit-enriched commit status list, or (None, exit_code) on error."""
    try:
        service = GerritService.from_cwd(cwd)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return None, 3

    try:
        commits = service.fetch_gerrit_data(commit_data, cwd=cwd)
    except GerritApiError as e:
        print(f"gerrit error: {e}", file=sys.stderr)
        return None, 3
    return commits, None


def _compute_url_start_visible(  # pylint: disable=too-many-arguments
    visible: list[LogCommit],
    *,
    show_url: bool,
    verbose: bool,
    summary_highlighter: SummaryHighlighter | None,
    show_change_id: bool,
    attn_col: int,
) -> int | None:
    """Compute visible column where URLs should start for compact one-line output."""
    if not show_url or verbose:
        return None
    widths = [
        visible_len(
            oneline_body(
                c,
                summary_highlighter=summary_highlighter,
                show_change_id=show_change_id,
                attention_col=attn_col,
            )
        )
        for c in visible
        if c.gerrit_url
    ]
    if not widths:
        return None
    return max(widths) + 2


def _render_text_output(  # pylint: disable=too-many-arguments,too-many-locals
    *,
    visible: list[LogCommit],
    verbose: bool,
    show_url: bool,
    show_change_id: bool,
    summary_highlighter: SummaryHighlighter | None,
) -> None:
    """Render text view for ``ger log``."""
    attn_col = attention_column(
        visible,
        summary_highlighter=summary_highlighter,
        show_change_id=show_change_id,
    )
    url_start_visible = _compute_url_start_visible(
        visible,
        show_url=show_url,
        verbose=verbose,
        summary_highlighter=summary_highlighter,
        show_change_id=show_change_id,
        attn_col=attn_col,
    )

    for commit in visible:
        if verbose:
            ind = " " * continuation_indent(commit)
            intro = oneline_line(
                commit,
                summary_highlighter=summary_highlighter,
                include_url=False,
                show_change_id=show_change_id,
                attention_col=attn_col,
            )
            print(intro)
            if show_url and commit.gerrit_url:
                print(f"{ind}{color_text(commit.gerrit_url, ANSI_DIM)}")
            for d in extra_detail_lines(commit):
                print(f"{ind}{d}")
        else:
            print(
                oneline_line(
                    commit,
                    summary_highlighter=summary_highlighter,
                    include_url=show_url,
                    show_change_id=show_change_id,
                    attention_col=attn_col,
                    url_start_visible=url_start_visible,
                )
            )


def main(argv: list[str] | None = None) -> int:  # pylint: disable=too-many-locals
    """CLI entry for ``ger log``: show local commits vs Gerrit labels, comments, and CI status."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    cwd, summary_highlighter = init_cli_runtime(debug_log=args.debug_log, color=args.color)

    gdef = log_defaults(cwd)
    verbose = bool(args.verbose)
    show_url = bool(args.url) or gdef["show_url"] or verbose
    show_change_id = bool(args.show_change_id) or gdef["show_change_id"]

    rev_range, rev_range_exit = resolve_rev_range(cwd, args.rev_range)
    if rev_range_exit is not None:
        return rev_range_exit
    assert rev_range is not None
    for branch in rev_range_needs_upstream_resolution(cwd, rev_range):
        if branch_has_upstream(cwd, branch):
            continue
        if not ensure_branch_upstream_interactive(cwd, branch) and sys.stdin.isatty():
            return 1

    commits, load_exit = load_annotated_commits(cwd, rev_range, first_parent=not args.follow_merges)
    if commits is None:
        return load_exit

    visible = commits
    has_attention = any(c.attention_reasons for c in commits)

    # JSON output
    if args.json_:
        payload = [
            {
                "sha": c.sha,
                "summary": c.summary,
                "pushed": c.pushed,
                "patchset_status": c.patchset_status,
                "verified": c.verified,
                "code_review": c.code_review,
                "comments_unresolved": c.comments_unresolved,
                "ci_failures": c.ci_failures,
                "gerrit_url": c.gerrit_url,
                "submittable": c.submittable,
                "change_id": c.change_id,
                "abandoned": c.abandoned,
                "attention_reasons": c.attention_reasons,
                "change_status": c.change_status,
                "merged_equivalent": c.merged_equivalent,
            }
            for c in visible
        ]
        print(json.dumps(payload, indent=2))
        return 1 if has_attention else 0

    _render_text_output(
        visible=visible,
        verbose=verbose,
        show_url=show_url,
        show_change_id=show_change_id,
        summary_highlighter=summary_highlighter,
    )

    summary, ready_n, total_n = _build_summary(commits)
    print()
    print(
        _format_summary_dashboard_line(
            summary,
            ready_n,
            total_n,
        )
    )

    return 1 if has_attention else 0


if __name__ == "__main__":
    raise SystemExit(main())
