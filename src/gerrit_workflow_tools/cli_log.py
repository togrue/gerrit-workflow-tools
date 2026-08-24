"""CLI for stack-aware Gerrit status over local commits."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from gerrit_workflow_tools.cli_common import (
    HELP_JSON,
    ExitCode,
    add_color_args,
    add_follow_merges_args,
    add_verbose_and_debug_log_args,
    init_cli_runtime,
    run_cli_command,
)
from gerrit_workflow_tools.cli_style import (
    ANSI_BOLD,
    ANSI_CYAN,
    ANSI_DIM,
    ANSI_GREEN,
    ANSI_RED,
    ANSI_YELLOW,
    color_text,
    format_link,
    is_hyperlink_enabled,
    visible_len,
)
from gerrit_workflow_tools.core.annotated_stack import (
    branches_needing_upstream,
    load_annotated_stack,
    resolve_rev_range,
)
from gerrit_workflow_tools.core.gerrit.change_resolution import resolve_stack_context
from gerrit_workflow_tools.core.gerrit.rest import GerritRest
from gerrit_workflow_tools.core.gerrit_change_status import LogCommit
from gerrit_workflow_tools.core.ready_strategy import ReadyCommitRow
from gerrit_workflow_tools.core.upstream_interactive import require_branch_upstream
from gerrit_workflow_tools.render.commit_row import (
    attention_column,
    continuation_indent,
    extra_detail_lines,
    oneline_body,
    oneline_line,
)
from gerrit_workflow_tools.summary_highlight import SummaryHighlighter, build_summary_highlighter

logger = logging.getLogger(__name__)


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
            "When OSC 8 hyperlinks are on, a compact ``Open in gerrit`` link is shown by default. "
            "Otherwise default: ``gerrit.logShowUrl``."
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
                print(f"{ind}{color_text(format_link(commit.gerrit_url), ANSI_DIM)}")
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


def main(argv: list[str] | None = None, *, gerrit: GerritRest | None = None) -> int:
    """CLI entry for ``ger log``: show local commits vs Gerrit labels, comments, and CI status."""
    return run_cli_command(lambda: _run(argv, gerrit=gerrit))


def _run(argv: list[str] | None, *, gerrit: GerritRest | None) -> int:  # pylint: disable=too-many-locals
    parser = _build_parser()
    args = parser.parse_args(argv)
    cwd, settings, summary_highlighter = init_cli_runtime(
        debug_log=args.debug_log, color=args.color, hyperlinks=args.hyperlinks
    )

    gdef = settings.log_defaults
    verbose = bool(args.verbose)
    show_url = bool(args.url) or gdef["show_url"] or verbose or is_hyperlink_enabled()
    show_change_id = bool(args.show_change_id) or gdef["show_change_id"]

    rev_range = resolve_rev_range(cwd, settings=settings, arg_rev_range=args.rev_range)
    for branch in branches_needing_upstream(cwd, rev_range, settings=settings):
        if not require_branch_upstream(cwd, branch, settings=settings):
            return int(ExitCode.ATTENTION)

    stack_view = load_annotated_stack(
        cwd, rev_range, settings=settings, first_parent=not args.follow_merges, gerrit=gerrit
    )
    if not stack_view.commits:
        print("(no commits in range)")
        return int(ExitCode.OK)

    commits = stack_view.commits
    notes_by_sha = stack_view.notes_by_sha
    stack = resolve_stack_context(cwd, settings=settings)
    summary_highlighter = build_summary_highlighter(
        settings,
        cwd=cwd,
        commits=[
            ReadyCommitRow(sha=c.sha, short_sha=c.short_sha, subject=c.summary, change_id=c.change_id)
            for c in commits
        ],
        project=stack.project,
        web_base=settings.gerrit_web_url,
    )
    use_color = args.color != "never"
    for commit in commits:
        note = notes_by_sha.get(commit.sha)
        if note:
            text = color_text(note, ANSI_DIM) if use_color else note
            print(text, file=sys.stderr)

    visible = commits
    has_attention = any(c.attention_reasons for c in commits)

    # JSON output
    if args.json_:
        stack = resolve_stack_context(cwd, settings=settings)
        stack_payload = {
            "project": stack.project,
            "target_branch": stack.target_branch,
            "push_branch": stack.push_branch,
        }

        commit_payload = [
            {
                "sha": c.sha,
                "summary": c.summary,
                "pushed": c.pushed,
                "patchset_status": c.patchset_status,
                "verified": c.verified,
                "code_review": c.code_review,
                "comments_unresolved": c.comments_unresolved,
                "ci_failures": c.ci_failures,
                "ci_links": [
                    {"label": link.label, "url": link.url, "source": link.source} for link in c.ci_links
                ],
                "gerrit_url": c.gerrit_url,
                "submittable": c.submittable,
                "change_id": c.change_id,
                "abandoned": c.abandoned,
                "attention_reasons": c.attention_reasons,
                "change_status": c.change_status,
                "merged_equivalent": c.merged_equivalent,
                **({"resolution_note": notes_by_sha[c.sha]} if c.sha in notes_by_sha else {}),
            }
            for c in visible
        ]
        payload = {"stack": stack_payload, "commits": commit_payload}
        print(json.dumps(payload, indent=2))
        return int(ExitCode.ATTENTION) if has_attention else int(ExitCode.OK)

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

    return int(ExitCode.ATTENTION) if has_attention else int(ExitCode.OK)


if __name__ == "__main__":
    raise SystemExit(main())
