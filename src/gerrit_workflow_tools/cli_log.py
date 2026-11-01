"""CLI for stack-aware Gerrit status over local commits."""

from __future__ import annotations

import argparse
import json
import logging

from gerrit_workflow_tools.cli_attention import require_stack_upstream
from gerrit_workflow_tools.cli_common import (
    ExitCode,
    add_color_args,
    add_follow_merges_args,
    add_json_arg,
    add_verbose_and_debug_log_args,
    init_cli_runtime,
    print_note,
    run_cli_command,
)
from gerrit_workflow_tools.cli_style import (
    ANSI_BOLD,
    ANSI_CYAN,
    ANSI_DIM,
    ANSI_GREEN,
    ANSI_RED,
    ANSI_YELLOW,
    color_stat,
    color_text,
    is_hyperlink_enabled,
    visible_len,
)
from gerrit_workflow_tools.core.annotated_stack import load_annotated_stack, resolve_rev_range
from gerrit_workflow_tools.core.attention import Attention
from gerrit_workflow_tools.core.gerrit.rest import GerritRest
from gerrit_workflow_tools.core.gerrit.stack_context import resolve_stack_context
from gerrit_workflow_tools.core.gerrit_change_status import LogCommit
from gerrit_workflow_tools.core.git_state import rebase_in_progress
from gerrit_workflow_tools.core.ready_strategy import blocked_shas_for_stack
from gerrit_workflow_tools.render.commit_row import (
    attention_column,
    continuation_indent,
    continuation_lines,
    oneline_body,
    oneline_line,
)
from gerrit_workflow_tools.summary_highlight import SummaryHighlighter, build_summary_highlighter


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def _build_summary(commits: list[LogCommit]) -> dict[str, int]:
    """Return per-category counts for summary printing."""
    counts: dict[str, int] = {
        "ci-failures": 0,
        Attention.UNRESOLVED_COMMENTS: 0,
        "on-gerrit": 0,
    }
    for c in commits:
        if c.pushed and Attention.CI_FAILED in c.attention_reasons:
            counts["ci-failures"] += 1
        if c.comments_unresolved > 0:
            counts[Attention.UNRESOLVED_COMMENTS] += 1
        if c.pushed:
            counts["on-gerrit"] += 1
    return counts


def _format_summary_dashboard_line(
    summary: dict[str, int],
    ready_n: int,
    total_n: int,
) -> str:
    """Single-line summary: ``summary: ready N/M · …`` with optional ANSI styling."""
    stats = [color_stat("ready ", f"{ready_n}/{total_n}", ANSI_GREEN)]
    for label, count, color in (
        ("CI ", summary.get("ci-failures", 0), ANSI_RED),
        ("comments ", summary.get(Attention.UNRESOLVED_COMMENTS, 0), ANSI_YELLOW),
        ("on-gerrit ", summary.get("on-gerrit", 0), ANSI_CYAN),
    ):
        if count:
            stats.append(color_stat(label, count, color))
    return f"{color_text('summary:', f'{ANSI_BOLD}{ANSI_CYAN}')} {color_text(' · ', ANSI_DIM).join(stats)}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the command-line parser for ``ger log``."""
    parser = argparse.ArgumentParser(
        prog="ger log",
        description="Compact, actionable overview of the local commit chain vs Gerrit.",
    )
    add_json_arg(parser)
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
        help="Show full Change-Id on an indented continuation line. Default: ``gerrit.logShowChangeId``.",
    )
    add_verbose_and_debug_log_args(
        parser,
        debug_log_help="Log git commands to stderr.",
        verbose_count=True,
        verbose_help=(
            "Add indented CI pipeline lines below each commit (``-vv`` also shows Change-Id). "
            "The primary line layout is unchanged. "
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
    summary_highlighter: SummaryHighlighter | None,
    attn_col: int,
) -> int | None:
    """Compute visible column where URLs should start for compact one-line output."""
    if not show_url:
        return None
    widths = [
        visible_len(
            oneline_body(
                c,
                summary_highlighter=summary_highlighter,
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
    verbose_level: int,
    show_url: bool,
    show_change_id: bool,
    summary_highlighter: SummaryHighlighter | None,
) -> None:
    """Render text view for ``ger log``."""
    attn_col = attention_column(
        visible,
        summary_highlighter=summary_highlighter,
    )
    url_start_visible = _compute_url_start_visible(
        visible,
        show_url=show_url,
        summary_highlighter=summary_highlighter,
        attn_col=attn_col,
    )

    for commit in visible:
        print(
            oneline_line(
                commit,
                summary_highlighter=summary_highlighter,
                include_url=show_url,
                attention_col=attn_col,
                url_start_visible=url_start_visible,
            )
        )
        detail_lines = continuation_lines(
            commit,
            verbose_level=verbose_level,
            show_change_id=show_change_id,
        )
        if detail_lines:
            ind = " " * continuation_indent(commit)
            for line in detail_lines:
                print(f"{ind}{line}")


def main(argv: list[str] | None = None, *, gerrit: GerritRest | None = None) -> int:
    """CLI entry for ``ger log``: show local commits vs Gerrit labels, comments, and CI status."""
    return run_cli_command(lambda: _run(argv, gerrit=gerrit))


def _run(argv: list[str] | None, *, gerrit: GerritRest | None) -> int:  # pylint: disable=too-many-locals
    parser = _build_parser()
    args = parser.parse_args(argv)
    cwd, settings, summary_highlighter = init_cli_runtime(args)

    gdef = settings.log_defaults
    verbose_level = int(args.verbose)
    show_url = bool(args.url) or gdef["show_url"] or is_hyperlink_enabled()
    show_change_id = bool(args.show_change_id) or gdef["show_change_id"] or verbose_level >= 2

    rev_range = resolve_rev_range(cwd, settings=settings, arg_rev_range=args.rev_range)
    if not require_stack_upstream(cwd, settings, rev_range):
        return int(ExitCode.ATTENTION)

    stack_view = load_annotated_stack(
        cwd,
        rev_range,
        settings=settings,
        first_parent=not args.follow_merges,
        gerrit=gerrit,
        fetch_ci_pipelines=verbose_level >= 1,
    )
    if not stack_view.commits:
        print("(no commits in range)")
        return int(ExitCode.OK)

    if rebase_in_progress(cwd):
        print_note("note: a rebase is in progress; this shows the branch as it was before the rebase.")
    commits = stack_view.commits
    notes_by_sha = stack_view.notes_by_sha
    stack = resolve_stack_context(cwd, settings=settings)
    summary_highlighter = build_summary_highlighter(
        settings,
        cwd=cwd,
        commits=stack_view.rows,
        project=stack.project,
        web_base=settings.gerrit_web_url,
    )
    for commit in commits:
        print_note(notes_by_sha.get(commit.sha))

    exit_code = ExitCode.ATTENTION if any(c.attention_reasons for c in commits) else ExitCode.OK

    # JSON output
    if args.json_:
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
                "ci_links": [{"label": link.label, "url": link.url, "source": link.source} for link in c.ci_links],
                "ci_pipelines": [
                    {
                        "label": pipe.label,
                        "state": pipe.state,
                        **({"url": pipe.url} if pipe.url else {}),
                    }
                    for pipe in c.ci_pipelines
                ],
                "gerrit_url": c.gerrit_url,
                "submittable": c.submittable,
                "work_in_progress": c.wip,
                "private": c.private,
                "change_id": c.change_id,
                "abandoned": c.abandoned,
                "attention_reasons": c.attention_reasons,
                "change_status": c.change_status,
                "merged_equivalent": c.merged_equivalent,
                **({"resolution_note": notes_by_sha[c.sha]} if c.sha in notes_by_sha else {}),
            }
            for c in commits
        ]
        payload = {"stack": stack_payload, "commits": commit_payload}
        print(json.dumps(payload, indent=2))
        return int(exit_code)

    _render_text_output(
        visible=commits,
        verbose_level=verbose_level,
        show_url=show_url,
        show_change_id=show_change_id,
        summary_highlighter=summary_highlighter,
    )

    # The same boundary `ger push` stops at, so the two commands cannot disagree.
    blocked = blocked_shas_for_stack(
        cwd,
        project=stack.project,
        commits=stack_view.rows,
        stop_pattern=settings.stop_pattern,
        settings=settings,
        web_base=settings.gerrit_web_url,
    )
    print()
    print(_format_summary_dashboard_line(_build_summary(commits), len(commits) - len(blocked), len(commits)))
    return int(exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
