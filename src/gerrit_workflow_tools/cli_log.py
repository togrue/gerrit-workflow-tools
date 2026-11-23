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
    add_output_format_args,
    add_verbose_and_debug_log_args,
    init_cli_runtime,
    may_prompt,
    output_format,
    run_cli_command,
)
from gerrit_workflow_tools.cli_notes import print_note
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
from gerrit_workflow_tools.core.gerrit_change_status import LogCommit, PatchsetStatus
from gerrit_workflow_tools.core.ready_strategy import blocked_shas_for_stack
from gerrit_workflow_tools.core.rebase_state import RebaseProgress, RebaseStop, rebase_progress
from gerrit_workflow_tools.core.rebasing_stack import RebaseStep, RebasingStack
from gerrit_workflow_tools.core.upstream_moved import UpstreamMoved, merged_same, upstream_moved
from gerrit_workflow_tools.render.ai_text import stack_ai_lines
from gerrit_workflow_tools.render.commit_row import (
    attention_column,
    continuation_indent,
    continuation_lines,
    oneline_body,
    oneline_line,
)
from gerrit_workflow_tools.render.show_json import log_commit_json, rebase_json, rebase_steps, upstream_json
from gerrit_workflow_tools.summary_highlight import SummaryHighlighter, build_summary_highlighter


logger = logging.getLogger(__name__)

# Readable without colour (piped, NO_COLOR): commits above it are what `ger push` sends.
READY_BOUNDARY_MARKER = "---- ready boundary: `ger push` sends the commits above ----"


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def _build_summary(commits: list[LogCommit]) -> dict[str, int]:
    """Return per-category counts for summary printing."""
    counts: dict[str, int] = {
        "ci-failures": 0,
        Attention.UNRESOLVED_COMMENTS: 0,
        "on-gerrit": 0,
        Attention.WIP: 0,
    }
    for c in commits:
        if c.pushed and Attention.CI_FAILED in c.attention_reasons:
            counts["ci-failures"] += 1
        if c.comments_unresolved > 0:
            counts[Attention.UNRESOLVED_COMMENTS] += 1
        if c.pushed:
            counts["on-gerrit"] += 1
        if Attention.WIP in c.attention_reasons:
            counts[Attention.WIP] += 1
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
        ("WIP ", summary.get(Attention.WIP, 0), ANSI_YELLOW),
    ):
        if count:
            stats.append(color_stat(label, count, color))
    return f"{color_text('summary:', f'{ANSI_BOLD}{ANSI_CYAN}')} {color_text(' · ', ANSI_DIM).join(stats)}"


def next_steps(commits: list[LogCommit], upstream: UpstreamMoved | None = None) -> list[str]:
    """One ``next:`` line per state the stack is in that has a ger command for it, oldest commit first."""
    lines: list[str] = []
    if upstream and upstream.behind:
        lines.append(f"next: {upstream.advice()}")
    merged = [c for c in commits if c.patchset_status in (PatchsetStatus.MERGED_SAME, PatchsetStatus.MERGED_DRIFT)]
    if merged and not (upstream and upstream.behind):
        lines.append(
            f"next: {merged[0].short_sha} is merged on Gerrit; drop it and rebase onto the target with "
            "`ger rebase --onto-remote --drop-merged-equivalent`"
        )
    wip = sum(1 for c in commits if c.pushed and Attention.WIP in c.attention_reasons)
    if wip:
        lines.append(f"next: {wip} WIP change{'' if wip == 1 else 's'} on Gerrit; ready them with `ger push --ready`")
    for reason, how in (
        (Attention.DIVERGED, "merge Gerrit's patchset into yours with `ger checkout change:{n} --update`"),
        (Attention.OUTDATED_PATCHSET, "take Gerrit's newer patchset with `ger checkout change:{n} --update`"),
    ):
        hit = next((c for c in commits if reason in c.attention_reasons and c.number), None)
        if hit is not None:
            lines.append(f"next: {hit.short_sha} {reason}: {how.format(n=hit.number)}")
    if any(Attention.CHAIN_BLOCKED in c.attention_reasons for c in commits):
        lines.append(
            "chain blocked: a change below is not submittable yet (votes, CI, comments); it clears when that one is"
        )
    return lines


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the command-line parser for ``ger log``."""
    parser = argparse.ArgumentParser(
        prog="ger log",
        description="Compact, actionable overview of the local commit chain vs Gerrit.",
    )
    add_output_format_args(parser, ai_view="one plain line per commit")
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
    blocked: frozenset[str] = frozenset(),
) -> None:
    """Render text view for ``ger log``, with a plain-text line where the ready boundary falls."""
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

    boundary = next((c.sha for c in visible if c.sha in blocked), None)
    for commit in visible:
        if commit.sha == boundary:
            print(color_text(READY_BOUNDARY_MARKER, ANSI_DIM))
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


_STOPS = {
    RebaseStop.EDIT: "stopped{at} to edit it",
    RebaseStop.SPLIT: (
        "stopped{at} to edit it, and HEAD has moved on from it: the commits above it were made at the stop"
    ),
    RebaseStop.CONFLICT: "stopped on a conflict{at} in {paths}",
    RebaseStop.RESOLVED: "stopped{at} with its conflicts resolved, ready for `git rebase --continue`",
    RebaseStop.PAUSED: "stopped between commits",
}


def _rebase_note(progress: RebaseProgress, rebasing: RebasingStack | None) -> str:
    """The note above a stack a rebase is underway on: whose, where it stopped, what is shown as it was."""
    current = rebasing.current if rebasing else None
    at = f' at {current.short_sha} "{current.subject}"' if current else ""
    if not current and progress.current and progress.stop is RebaseStop.SPLIT:
        at = f" at {progress.current[:7]}"
    stop = progress.stop
    said = _STOPS[stop].format(at=at, paths=", ".join(progress.unmerged)) if stop else "stopped"
    whose = f"note: a rebase is in progress on {progress.branch or 'a detached HEAD'}, {said}"
    if rebasing is None:
        # No rebasing stack: the backend keeps no todo (the branch as it was), or a range was asked for (as it is).
        shown = (
            "the branch as it was before the rebase" if not progress.keeps_todo else "the range asked for, as it is now"
        )
        return f"{whose}; this shows {shown}."
    todo = rebasing.steps.count(RebaseStep.TODO)
    rest = f"; {todo} commit{'s' if todo != 1 else ''} still to replay, shown as before" if todo else ""
    return f"{whose}{rest}."


def main(argv: list[str] | None = None, *, gerrit: GerritRest | None = None) -> int:
    """CLI entry for ``ger log``: show local commits vs Gerrit labels, comments, and CI status."""
    return run_cli_command(lambda: _run(argv, gerrit=gerrit))


def _run(argv: list[str] | None, *, gerrit: GerritRest | None) -> int:  # pylint: disable=too-many-locals
    parser = _build_parser()
    args = parser.parse_args(argv)
    run, summary_highlighter = init_cli_runtime(args, gerrit=gerrit)
    settings = run.settings

    gdef = settings.log_defaults
    verbose_level = int(args.verbose)
    show_url = bool(args.url) or gdef["show_url"] or is_hyperlink_enabled()
    show_change_id = bool(args.show_change_id) or gdef["show_change_id"] or verbose_level >= 2

    rev_range = resolve_rev_range(args.rev_range, branch=run.repo.stack_branch)
    if not require_stack_upstream(run, rev_range, interactive=may_prompt(args)):
        return int(ExitCode.REFUSED)

    stack_view = load_annotated_stack(
        run, args.rev_range and rev_range, first_parent=not args.follow_merges, fetch_ci_pipelines=verbose_level >= 1
    )
    if not stack_view.commits:
        print("(no commits in range)")
        return int(ExitCode.OK)

    progress = (
        stack_view.rebase.rebase
        if stack_view.rebase
        else rebase_progress(run.cwd, repo_dir=run.worktree.repository_dir)
    )
    if progress is not None:
        print_note(_rebase_note(progress, stack_view.rebase))
    # The default stack only, and not during a rebase: that one is finished first.
    on_stack = args.rev_range is None and progress is None
    merged, branch = merged_same(stack_view.commits), run.repo.working_branch
    moved = upstream_moved(run.cwd, branch, stack_view.rows, settings=settings, merged=merged) if on_stack else None
    commits = stack_view.commits
    notes_by_sha = stack_view.notes_by_sha
    stack = run.repo.shown_stack
    summary_highlighter = build_summary_highlighter(
        settings,
        root=run.repo.root,
        commits=stack_view.rows,
        project=stack.project,
        web_base=settings.gerrit_web_url,
    )
    for commit in commits:
        print_note(notes_by_sha.get(commit.sha))

    out_fmt = output_format(args)
    if out_fmt == "json":
        stack_payload = {
            "project": stack.project,
            "target_branch": stack.target_branch or None,
            "push_branch": stack.push_branch or None,
        }

        commit_payload = [
            {
                **log_commit_json(c),
                "abandoned": c.abandoned,
                "rebase": step,
                **({"resolution_note": notes_by_sha[c.sha]} if c.sha in notes_by_sha else {}),
            }
            for c, step in zip(commits, rebase_steps(stack_view), strict=True)
        ]
        rebase = rebase_json(stack_view, progress)
        payload = {
            "stack": stack_payload,
            "rebase": rebase,
            "upstream": upstream_json(moved),
            "commits": commit_payload,
        }
        print(json.dumps(payload, indent=2))
        if stack_view.gerrit_unavailable is not None:
            raise stack_view.gerrit_unavailable
        return int(ExitCode.OK)

    # The same boundary `ger push` stops at, so the two commands cannot disagree.
    blocked = blocked_shas_for_stack(
        run.repo.root,
        project=stack.project,
        commits=stack_view.rows,
        stop_pattern=settings.stop_pattern,
        settings=settings,
        web_base=settings.gerrit_web_url,
    )
    if out_fmt == "ai":
        ready = len(commits) - len(blocked)
        lines = stack_ai_lines(
            commits, project=stack.project, branch=stack.push_branch, ready=ready, next_lines=next_steps(commits, moved)
        )
        print("\n".join(lines))
    else:
        _render_text_output(
            visible=commits,
            verbose_level=verbose_level,
            show_url=show_url,
            show_change_id=show_change_id,
            summary_highlighter=summary_highlighter,
            blocked=blocked,
        )
        print()
        print(_format_summary_dashboard_line(_build_summary(commits), len(commits) - len(blocked), len(commits)))
        for line in next_steps(commits, moved):
            print(color_text(line, ANSI_DIM))
    if stack_view.gerrit_unavailable is not None:
        raise stack_view.gerrit_unavailable
    return int(ExitCode.OK)


if __name__ == "__main__":
    raise SystemExit(main())
