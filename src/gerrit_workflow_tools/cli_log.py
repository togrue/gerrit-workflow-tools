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
    ANSI_LIGHT_GREEN,
    ANSI_RED,
    ANSI_RESET,
    ANSI_STRIKE,
    ANSI_YELLOW,
    color_short_sha,
    color_text,
    is_color_enabled,
    visible_len,
)
from gerrit_workflow_tools.core.config import current_branch, log_defaults
from gerrit_workflow_tools.core.gerrit_change_status import (
    CommitStatusInput,
    LogCommit,
    commit_blocks_chain_for_submittability,
    determine_attention,
    fetch_gerrit_data,
)
from gerrit_workflow_tools.core.gerrit_client import GerritApiError, GerritClient, resolve_gerrit_web_base
from gerrit_workflow_tools.core.git_run import GitError, git_out
from gerrit_workflow_tools.core.stack import commits_in_range
from gerrit_workflow_tools.core.upstream_interactive import branch_has_upstream, ensure_branch_upstream_interactive
from gerrit_workflow_tools.summary_highlight import SummaryHighlighter

logger = logging.getLogger(__name__)
_UPSTREAM_TOKEN_RE = re.compile(r"(?P<branch>[^\s@]+)?@\{upstream\}")

# Fixed width for the abbreviated SHA so status columns line up across commits.
_STATUS_SHA_COL_WIDTH = 8


def _status_sha_column(short_sha: str) -> str:
    """Left-justify short SHA for a stable column (typical git abbrev is 7 characters)."""
    return short_sha.ljust(_STATUS_SHA_COL_WIDTH)


def _fmt_summary_strike(summary: str) -> str:
    """Strike through the commit summary (ANSI SGR 9, or combining chars without a TTY)."""
    if is_color_enabled():
        return f"{ANSI_STRIKE}{summary}{ANSI_RESET}"
    return "".join(f"{c}\u0336" for c in summary)


def _annotate_attention(commits: list[LogCommit]) -> None:
    """Populate attention_reasons on each commit, including chain-blocking.

    Chain-blocking: an earlier pushed commit blocks later ones when
    :func:`~gerrit_workflow_tools.gerrit_change_status.commit_blocks_chain_for_submittability`
    says so (Gerrit submittable, plus MERGED equivalence rules).
    """
    prefix_chain_blocks = False
    for commit in commits:
        chain_blocked = commit.pushed and prefix_chain_blocks
        commit.attention_reasons = determine_attention(commit, chain_blocked=chain_blocked)
        if commit.pushed and commit_blocks_chain_for_submittability(commit):
            prefix_chain_blocks = True


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def _fmt_patchset_column(commit: LogCommit) -> str:  # pylint: disable=too-many-return-statements
    """Single-letter column: current patch set / local ahead / outdated / not on Gerrit."""
    if commit.abandoned:
        return color_text("a", ANSI_DIM)
    status = commit.patchset_status
    if status == "merged-same":
        return color_text("m", ANSI_GREEN)
    if status == "merged-drift":
        return color_text("!", ANSI_RED)
    if status == "merged-unknown":
        return color_text("?", ANSI_YELLOW)
    if status == "active":
        return color_text("p", ANSI_GREEN)
    if status == "newer":
        return color_text("n", ANSI_YELLOW)
    if status == "outdated":
        return color_text("o", ANSI_RED)
    return color_text("-", ANSI_DIM)


def _fmt_verified(v: int | None) -> str:
    if v is None:
        return color_text("v? ", ANSI_DIM)
    if v >= 1:
        return color_text("v+1", ANSI_GREEN)
    if v <= -1:
        return color_text("v-1", ANSI_RED)
    return color_text("v0 ", ANSI_DIM)


def _fmt_code_review(cr: int | None) -> str:
    if cr is None:
        return color_text("cr? ", ANSI_DIM)
    if cr >= 2:
        return color_text("cr+2", ANSI_GREEN)
    if cr == 1:
        return color_text("cr+1", ANSI_LIGHT_GREEN)
    if cr == -1:
        return color_text("cr-1", ANSI_YELLOW)
    if cr <= -2:
        return color_text("cr-2", ANSI_RED)
    return color_text("cr0 ", ANSI_DIM)


def _fmt_comments(count: int) -> str:
    if count > 0:
        return color_text("com", ANSI_YELLOW)
    return color_text("   ", ANSI_DIM)


def _primary_line_prefix(commit: LogCommit) -> str:
    """Text before the subject on the primary line (through ``  # ``), same as in :func:`_primary_line`."""
    sha = color_short_sha(_status_sha_column(commit.short_sha))
    push = _fmt_patchset_column(commit)
    verified = _fmt_verified(commit.verified)
    cr = _fmt_code_review(commit.code_review)
    comments = _fmt_comments(commit.comments_unresolved)
    return f"{sha} {push} {verified} {cr} {comments} # "


def _continuation_indent(commit: LogCommit) -> int:
    """Column where the subject starts; continuation lines align using :func:`visible_len` on the prefix."""
    return visible_len(_primary_line_prefix(commit))


def _extra_detail_lines(commit: LogCommit) -> list[str]:
    """Indented detail lines only where they add information not already on the oneline row.

    Attention tokens (``# build failed``, unresolved comment counts, etc.) stay on the
    oneline introduction via :func:`_oneline_line`. Here we add structured CI failure
    names (room for per-check URLs later) and avoid repeating comment counts.
    """
    failures = commit.ci_failures
    if not failures:
        return []
    if len(failures) == 1:
        return [color_text(f"# failed: {failures[0]}", ANSI_RED)]
    lines: list[str] = [color_text("# failed checks:", ANSI_RED)]
    for name in failures:
        lines.append(color_text(f"  · {name}", ANSI_RED))
    return lines


def _commit_body_detail_lines(cwd: Path | str, commit: LogCommit) -> list[str]:
    """Non-first lines of the commit message (subject already on the oneline row)."""
    try:
        raw = git_out("log", "-1", "--format=%B", commit.sha, cwd=cwd)
    except GitError:
        return []
    lines = raw.splitlines()
    if lines and lines[0].strip() == commit.summary.strip():
        lines = lines[1:]
    while lines and not lines[0].strip():
        lines.pop(0)
    return [ln if not ln.strip() else color_text(ln, ANSI_DIM) for ln in lines]


def _fmt_change_id_suffix(change_id: str | None) -> str:
    if not change_id:
        return ""
    disp = change_id if len(change_id) <= 14 else change_id[:12] + "…"
    return color_text(f"  {disp}", ANSI_DIM)


def _primary_line(
    commit: LogCommit,
    *,
    summary_highlighter: SummaryHighlighter | None = None,
    show_change_id: bool = False,
) -> str:
    summ = _fmt_summary_strike(commit.summary) if commit.abandoned else commit.summary
    if summary_highlighter is not None and not commit.abandoned:
        summ = summary_highlighter.highlight(summ)
    line = f"{_primary_line_prefix(commit)}{summ}"
    if show_change_id:
        line += _fmt_change_id_suffix(commit.change_id)
    return line


def _attention_tokens(commit: LogCommit) -> list[tuple[str, str]]:
    if commit.abandoned:
        return [("abandoned", ANSI_RED)]
    if commit.patchset_status == "merged-drift":
        return [("merged drift", ANSI_RED)]
    if commit.patchset_status == "merged-unknown":
        return [("merged (equiv. unknown)", ANSI_YELLOW)]
    if commit.patchset_status == "merged-same":
        return []

    tokens: list[tuple[str, str]] = []
    if commit.ci_failures or (commit.verified is not None and commit.verified <= -1):
        tokens.append(("build failed", ANSI_RED))
    if commit.comments_unresolved > 0:
        noun = "comment" if commit.comments_unresolved == 1 else "comments"
        tokens.append((f"{commit.comments_unresolved} unresolved {noun}", ANSI_YELLOW))
    if commit.submittable and not tokens:
        tokens.append(("submittable", ANSI_GREEN))
    return tokens


def _attention_suffix(commit: LogCommit) -> str:
    tokens = _attention_tokens(commit)
    if not tokens:
        return ""

    rendered: list[str] = [color_text("# ", ANSI_DIM)]
    for idx, (text, code) in enumerate(tokens):
        if idx:
            rendered.append(color_text(", ", ANSI_DIM))
        rendered.append(color_text(text, code))
    return "".join(rendered)


def _attention_column(
    commits: list[LogCommit],
    *,
    summary_highlighter: SummaryHighlighter | None = None,
    show_change_id: bool = False,
) -> int:
    widths = [
        visible_len(
            _primary_line(
                commit,
                summary_highlighter=summary_highlighter,
                show_change_id=show_change_id,
            )
        )
        for commit in commits
        if _attention_tokens(commit)
    ]
    if not widths:
        return 0
    return max(widths) + 2


def _oneline_body(
    commit: LogCommit,
    *,
    summary_highlighter: SummaryHighlighter | None = None,
    show_change_id: bool = False,
    attention_column: int = 0,
) -> str:
    """Oneline text through attention suffix; excludes Gerrit URL."""
    base = _primary_line(
        commit,
        summary_highlighter=summary_highlighter,
        show_change_id=show_change_id,
    )
    attention = _attention_suffix(commit)
    if attention:
        gap = max(2, attention_column - visible_len(base)) if attention_column else 2
        base = f"{base}{' ' * gap}{attention}"
    return base


def _oneline_line(  # pylint: disable=too-many-arguments
    commit: LogCommit,
    *,
    summary_highlighter: SummaryHighlighter | None = None,
    include_url: bool,
    show_change_id: bool = False,
    attention_column: int = 0,
    url_start_visible: int | None = None,
) -> str:
    body = _oneline_body(
        commit,
        summary_highlighter=summary_highlighter,
        show_change_id=show_change_id,
        attention_column=attention_column,
    )
    if include_url and commit.gerrit_url:
        if url_start_visible is not None:
            pad = url_start_visible - visible_len(body)
            pad = max(pad, 2)
            return f"{body}{' ' * pad}{color_text(commit.gerrit_url, ANSI_DIM)}"
        return f"{body}  {color_text(commit.gerrit_url, ANSI_DIM)}"
    return body


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
    parser.add_argument(
        "--filter-attention",
        action="store_true",
        help="Show only attention-required commits.",
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
        verbose_action="count",
        verbose_help=(
            "Expanded layout: oneline summary, indented details, Gerrit URL on the next line when URLs are on. "
            "``-vv`` adds the commit message body. "
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


def _resolve_rev_range(cwd: Path, arg_rev_range: str | None) -> tuple[str | None, int | None]:
    """Return revision range or (None, exit_code) on error."""
    if arg_rev_range:
        if ".." in arg_rev_range:
            return arg_rev_range, None
        return f"{arg_rev_range}@{{upstream}}..{arg_rev_range}", None
    try:
        branch = current_branch(cwd)
    except GitError as e:
        print(f"error: {e}", file=sys.stderr)
        return None, 2
    if branch == "HEAD":
        return "@{upstream}..HEAD", None
    return f"{branch}@{{upstream}}..{branch}", None


def rev_range_needs_upstream_resolution(cwd: Path, rev_range: str) -> list[str]:
    """Return branch names that need upstream resolution for *rev_range*."""
    current = current_branch(cwd)
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
        web_base = resolve_gerrit_web_base(cwd)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return None, 3

    client = GerritClient(web_base, cwd=str(cwd))
    try:
        commits = fetch_gerrit_data(client, web_base, commit_data, cwd=cwd)
    except GerritApiError as e:
        print(f"gerrit error: {e}", file=sys.stderr)
        return None, 3
    return commits, None


def _compute_url_start_visible(  # pylint: disable=too-many-arguments
    visible: list[LogCommit],
    *,
    show_url: bool,
    log_verbosity: int,
    summary_highlighter: SummaryHighlighter | None,
    show_change_id: bool,
    attention_column: int,
) -> int | None:
    """Compute visible column where URLs should start for compact one-line output."""
    if not show_url or log_verbosity >= 1:
        return None
    widths = [
        visible_len(
            _oneline_body(
                c,
                summary_highlighter=summary_highlighter,
                show_change_id=show_change_id,
                attention_column=attention_column,
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
    cwd: Path,
    commits: list[LogCommit],
    visible: list[LogCommit],
    log_verbosity: int,
    show_url: bool,
    show_change_id: bool,
    filter_attention: bool,
    summary_highlighter: SummaryHighlighter | None,
) -> None:
    """Render text view for ``ger log``."""
    non_attention_filtered = len(commits) - len(visible)
    if filter_attention and non_attention_filtered > 0:
        noun = "commit" if non_attention_filtered == 1 else "commits"
        print(f"... {non_attention_filtered} non-attention {noun}")

    attention_column = _attention_column(
        visible,
        summary_highlighter=summary_highlighter,
        show_change_id=show_change_id,
    )
    url_start_visible = _compute_url_start_visible(
        visible,
        show_url=show_url,
        log_verbosity=log_verbosity,
        summary_highlighter=summary_highlighter,
        show_change_id=show_change_id,
        attention_column=attention_column,
    )

    for commit in visible:
        if log_verbosity >= 1:
            ind = " " * _continuation_indent(commit)
            intro = _oneline_line(
                commit,
                summary_highlighter=summary_highlighter,
                include_url=False,
                show_change_id=show_change_id,
                attention_column=attention_column,
            )
            print(intro)
            if show_url and commit.gerrit_url:
                print(f"{ind}{color_text(commit.gerrit_url, ANSI_DIM)}")
            for d in _extra_detail_lines(commit):
                print(f"{ind}{d}")
            if log_verbosity >= 2:
                for b in _commit_body_detail_lines(cwd, commit):
                    print(f"{ind}{b}")
        else:
            print(
                _oneline_line(
                    commit,
                    summary_highlighter=summary_highlighter,
                    include_url=show_url,
                    show_change_id=show_change_id,
                    attention_column=attention_column,
                    url_start_visible=url_start_visible,
                )
            )


def main(argv: list[str] | None = None) -> int:  # pylint: disable=too-many-locals
    """CLI entry for ``ger log``: show local commits vs Gerrit labels, comments, and CI status."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    cwd, summary_highlighter = init_cli_runtime(debug_log=args.debug_log, color=args.color)

    gdef = log_defaults(cwd)
    log_verbosity = int(args.verbose)
    show_url = bool(args.url) or gdef["show_url"] or (log_verbosity >= 1)
    show_change_id = bool(args.show_change_id) or gdef["show_change_id"]

    rev_range, rev_range_exit = _resolve_rev_range(cwd, args.rev_range)
    if rev_range_exit is not None:
        return rev_range_exit
    assert rev_range is not None
    for branch in rev_range_needs_upstream_resolution(cwd, rev_range):
        if branch_has_upstream(cwd, branch):
            continue
        if not ensure_branch_upstream_interactive(cwd, branch) and sys.stdin.isatty():
            return 1

    commit_data, commit_data_exit = _load_commits_in_range(cwd, rev_range, first_parent=not args.follow_merges)
    if commit_data is None:
        return commit_data_exit

    commits, gerrit_exit = _fetch_enriched_commits(cwd, commit_data)
    if gerrit_exit is not None:
        return gerrit_exit
    assert commits is not None

    _annotate_attention(commits)

    visible = [c for c in commits if c.attention_reasons] if args.filter_attention else commits
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
        cwd=cwd,
        commits=commits,
        visible=visible,
        log_verbosity=log_verbosity,
        show_url=show_url,
        show_change_id=show_change_id,
        filter_attention=args.filter_attention,
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
