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
from gerrit_workflow_tools.core.annotated_stack import load_stack_overview, resolve_rev_range
from gerrit_workflow_tools.core.gerrit.rest import GerritRest
from gerrit_workflow_tools.render.ai_text import stack_ai_lines
from gerrit_workflow_tools.render.show_json import (
    log_commit_json,
    next_steps_json,
    rebase_json,
    rebase_steps,
    upstream_json,
)
from gerrit_workflow_tools.render.stack_log import (
    RowOptions,
    footer_lines,
    next_steps,
    rebase_note,
    row_lines,
    summary_line,
)
from gerrit_workflow_tools.render.status_fmt import status_legend
from gerrit_workflow_tools.render.style import is_hyperlink_enabled, terminal_width
from gerrit_workflow_tools.summary_highlight import run_summary_highlighter


logger = logging.getLogger(__name__)

_EXAMPLES = """examples:
  ger log                    the stack above its upstream, and what to do next
  ger log -v                 also each commit's CI links and reviewers
  ger log origin/main..HEAD  a range of commits
  ger log --ai               one plain line per commit, for an agent"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the command-line parser for ``ger log``."""
    parser = argparse.ArgumentParser(
        prog="ger log",
        description="Compact, actionable overview of the local stack vs Gerrit.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"{_EXAMPLES}\n\n{status_legend()}",
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
            "Otherwise default: ``ger.logShowUrl``."
        ),
    )
    parser.add_argument(
        "--show-change-id",
        action="store_true",
        help="Show full Change-Id on an indented continuation line. Default: ``ger.logShowChangeId``.",
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


def main(argv: list[str] | None = None, *, gerrit: GerritRest | None = None) -> int:
    """CLI entry for ``ger log``: show local commits vs Gerrit labels, comments, and CI status."""
    return run_cli_command(lambda: _run(argv, gerrit=gerrit))


def _run(argv: list[str] | None, *, gerrit: GerritRest | None) -> int:  # pylint: disable=too-many-locals
    parser = _build_parser()
    args = parser.parse_args(argv)
    run = init_cli_runtime(args, gerrit=gerrit)
    settings = run.settings

    gdef = settings.log_defaults
    verbose_level = int(args.verbose)
    show_url = bool(args.url) or gdef["show_url"] or is_hyperlink_enabled()
    show_change_id = bool(args.show_change_id) or gdef["show_change_id"] or verbose_level >= 2

    branch = run.repo.stack_branch if args.rev_range else run.repo.require_stack_branch()
    rev_range = resolve_rev_range(args.rev_range, branch=branch)
    if not require_stack_upstream(run, rev_range, interactive=may_prompt(args)):
        return int(ExitCode.REFUSED)

    overview = load_stack_overview(
        run, args.rev_range and rev_range, first_parent=not args.follow_merges, fetch_ci_pipelines=verbose_level >= 1
    )
    stack_view, progress, moved, blocked = overview.view, overview.progress, overview.moved, overview.blocked
    if not stack_view.commits:
        print(f"nothing above {moved.name}" if moved is not None else f"no commits in {rev_range}")
        return int(ExitCode.OK)

    if progress is not None:
        print_note(rebase_note(progress, stack_view.rebase))
    commits = stack_view.commits
    notes_by_sha = stack_view.notes_by_sha
    stack = run.repo.shown_stack
    summary_highlighter = run_summary_highlighter(run, blocked=blocked)
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
            "next": next_steps_json(overview),
        }
        print(json.dumps(payload, indent=2))
        if stack_view.gerrit_unavailable is not None:
            raise stack_view.gerrit_unavailable
        return int(ExitCode.OK)

    if out_fmt == "ai":
        lines = stack_ai_lines(
            commits,
            project=stack.project,
            branch=stack.push_branch,
            upload=overview.to_upload,
            next_lines=next_steps(overview),
        )
        print("\n".join(lines))
    else:
        width = terminal_width()
        options = RowOptions(verbose_level, show_url, show_change_id, width)
        for line in row_lines(commits, options, summary_highlighter=summary_highlighter, blocked=blocked):
            print(line)
        print()
        print(summary_line(overview, width=width))
        for line in footer_lines(next_steps(overview), width=width):
            print(line)
    if stack_view.gerrit_unavailable is not None:
        raise stack_view.gerrit_unavailable
    return int(ExitCode.OK)


if __name__ == "__main__":
    raise SystemExit(main())
