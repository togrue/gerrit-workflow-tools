"""CLI for ``ger show``: commit(s) vs Gerrit (status + inline comments)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from gerrit_workflow_tools.cli_args import (
    add_branch_arg,
    add_color_args,
    add_on_off_arg,
    add_output_format_args,
    add_stack_arg,
    add_verbose_and_debug_log_args,
    changeish_help,
    output_format,
)
from gerrit_workflow_tools.cli_attention import add_attention_query_args, show_revs_with_attention
from gerrit_workflow_tools.cli_common import ExitCode, init_cli_runtime, run_cli_command
from gerrit_workflow_tools.cli_notes import print_resolution_note
from gerrit_workflow_tools.core.annotated_targets import local_stack_or_none
from gerrit_workflow_tools.core.attention import Attention
from gerrit_workflow_tools.core.ci_content_service import CiContentService
from gerrit_workflow_tools.core.ci_http import CiHttp
from gerrit_workflow_tools.core.comment_chains import COMMENT_SELECTIONS, CommentSelection
from gerrit_workflow_tools.core.gerrit.change_resolution import ChangeResolutionError
from gerrit_workflow_tools.core.gerrit.rest import GerritRest
from gerrit_workflow_tools.core.gerrit.stack_context import resolve_stack_context
from gerrit_workflow_tools.core.gerrit_change_status import LogCommit
from gerrit_workflow_tools.core.gerrit_show import ShownCommit, load_shown_commits, resolve_show_targets
from gerrit_workflow_tools.core.review_chain import format_age
from gerrit_workflow_tools.core.stack import read_commit_meta
from gerrit_workflow_tools.render.ci_content import format_ci_section_human
from gerrit_workflow_tools.render.comments import (
    box_width,
    empty_comments_message,
    format_unresolved_section_human,
)
from gerrit_workflow_tools.render.commit_row import attention_summary, continuation_lines, show_headline, votes_text
from gerrit_workflow_tools.render.download_meter import DownloadMeter
from gerrit_workflow_tools.render.show_json import commit_json_payload
from gerrit_workflow_tools.render.show_markdown import commit_markdown
from gerrit_workflow_tools.render.status_fmt import chain_blocked_text
from gerrit_workflow_tools.render.style import ANSI_DIM, ANSI_YELLOW, color_text, format_link, terminal_width
from gerrit_workflow_tools.render.wrap import wrap_lines
from gerrit_workflow_tools.summary_highlight import SummaryHighlighter, run_summary_highlighter
from gerrit_workflow_tools.upstream_interactive import require_branch_upstream


_COMMIT_SEPARATOR = "═" * 64
DEFAULT_SHOW_CONTEXT_PADDING = 2

logger = logging.getLogger(__name__)


def _requested_context_padding(args: argparse.Namespace) -> int | None:
    if args.context_padding is not None:
        return args.context_padding
    if args.context:
        return DEFAULT_SHOW_CONTEXT_PADDING
    return None


def _context_padding_value(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid context padding: {raw!r}") from exc
    if value < 0:
        raise argparse.ArgumentTypeError("context padding must be >= 0")
    return value


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the command-line parser for ``ger show``."""
    p = argparse.ArgumentParser(
        prog="ger show",
        description="Show commit(s) and Gerrit status (labels, comments, CI).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  ger show                  HEAD: votes, attention and unresolved comments (with their ids)
  ger show HEAD~1 --context a commit, with the code around each comment
  ger show change:1234      a change by number, whether or not you have it locally
  ger show --stack          every commit of the local stack
  ger reply ID --done       answer a comment by the id ger show prints

The status columns are those of ger log (see ger log --help).""",
    )
    p.add_argument(
        "revs",
        nargs="*",
        metavar="REV",
        help=changeish_help("A commit or change, or an A..B / A...B range of them (default: HEAD)"),
    )
    add_stack_arg(p)
    add_attention_query_args(p)
    add_branch_arg(p)
    p.add_argument(
        "--comments",
        choices=COMMENT_SELECTIONS,
        default="unresolved",
        help="Which inline comment chains to show (default: unresolved).",
    )
    p.add_argument(
        "--context",
        action="store_true",
        help="Include source lines around each comment (Gerrit enable-context; padding 2).",
    )
    p.add_argument(
        "--context-padding",
        type=_context_padding_value,
        default=None,
        metavar="N",
        help="Extra source lines before/after the comment (implies --context; default 2).",
    )
    add_on_off_arg(
        p,
        "--ci",
        help_text="Attach CI findings from failed CI links (fetches CI logs; default: ger.showCiContent, off).",
    )
    add_output_format_args(p, ai_view="markdown")
    add_color_args(p)
    add_verbose_and_debug_log_args(
        p,
        verbose_help="Also fetch each change's CI pipelines (Checks) and list them under the commit.",
    )
    return p


def _print_wrapped_body(
    body: str,
    *,
    width: int,
    sha: str | None,
    summary_highlighter: SummaryHighlighter,
) -> None:
    """Print a commit message, wrapping each line and highlighting the subject."""
    body_lines = body.splitlines()
    if not body_lines:
        return
    print()
    for index, line in enumerate(body_lines):
        parts = wrap_lines(line, width)
        if index == 0:
            parts = [summary_highlighter.highlight(part, sha=sha) for part in parts]
        for part in parts:
            print(f"    {part}")


def _emit_human_commit(
    cwd: Path | str | None,
    item: ShownCommit,
    *,
    summary_highlighter: SummaryHighlighter,
    wrap_width: int,
) -> None:
    commit, chains, ci_contents = item.commit, item.chains, item.ci_contents
    is_local = item.target.is_local_commit
    print(show_headline(commit))
    # A blocked commit names its blocker on a line of its own, so the reasons need not say chain-blocked too.
    said = frozenset({Attention.CHAIN_BLOCKED}) if item.blocked_by is not None else frozenset()
    if commit.attention_reasons and (summary := attention_summary(commit, leave_out=said)):
        print(color_text(summary, ANSI_DIM))
    if item.blocked_by is not None:
        print(color_text(chain_blocked_text(item.blocked_by), ANSI_DIM))

    body = ""
    if is_local and commit.sha:
        meta_row = read_commit_meta(cwd, commit.sha)
        body = meta_row.body
        print(f"Author: {meta_row.author_line}{_when(meta_row.date)}")

    if commit.gerrit_url:
        print(f"{color_text('url:', ANSI_DIM)} {color_text(format_link(commit.gerrit_url), ANSI_YELLOW)}")
    if votes := votes_text(commit):
        print(f"{color_text('votes:', ANSI_DIM)} {votes}")
    for line in continuation_lines(commit, verbose_level=1):
        print(line)

    if body:
        _print_wrapped_body(
            body,
            width=wrap_width,
            sha=commit.sha,
            summary_highlighter=summary_highlighter,
        )
    elif commit.summary:
        print(f"\n    {commit.summary}")

    if ci_contents:
        print(f"\n{color_text('CI findings:', ANSI_DIM)}")
        for line in format_ci_section_human(ci_contents):
            print(line)

    comment_lines = format_unresolved_section_human(
        chains,
        commit.gerrit_url,
        pushed=commit.pushed,
        width=box_width(wrap_width, terminal_width()),
    )
    if comment_lines:
        print()
        for line in comment_lines:
            print(line)


def _when(iso_date: str) -> str:
    """``, 3h ago (2026-11-24 04:39)`` for an ISO author date; empty when there is none."""
    try:
        when = datetime.fromisoformat(iso_date)
    except ValueError:
        return f", {iso_date}" if iso_date else ""
    age = format_age(int((datetime.now(timezone.utc) - when).total_seconds()))
    return f", {age} ago ({when:%Y-%m-%d %H:%M})"


def _emit_brief_commit(commit: LogCommit, *, comments: CommentSelection) -> None:
    """One commit of a multi-commit view that has no comments to expand."""
    print(show_headline(commit))
    print(f"    {commit.summary}")
    print(color_text(empty_comments_message(comments), ANSI_DIM))


def main(argv: list[str] | None = None, *, gerrit: GerritRest | None = None, ci_http: CiHttp | None = None) -> int:
    """Resolve revision(s) and print human, Markdown, or JSON Gerrit status details."""
    return run_cli_command(lambda: _run(argv, gerrit=gerrit, ci_http=ci_http))


def _run(  # pylint: disable=too-many-branches,too-many-locals,too-many-statements
    argv: list[str] | None,
    *,
    gerrit: GerritRest | None,
    ci_http: CiHttp | None,
) -> int:
    p = _build_parser()
    args = p.parse_args(argv)
    run = init_cli_runtime(args, gerrit=gerrit)
    summary_highlighter = run_summary_highlighter(run)
    cwd, settings = run.cwd, run.settings
    out_fmt = output_format(args)
    verbose_level = int(args.verbose)
    branch = args.branch or run.repo.working_branch

    if (
        (args.stack or args.first_attention or args.next_attention)
        and branch is not None
        and not require_branch_upstream(cwd, branch, settings=settings)
    ):
        return int(ExitCode.REFUSED)

    revs_or_code = show_revs_with_attention(p, args, run=run, branch=branch)
    if isinstance(revs_or_code, int):
        return revs_or_code

    service = run.gerrit
    targets = resolve_show_targets(
        cwd,
        revs_or_code,
        service.rest,
        settings=settings,
        stack=args.stack,
        branch=branch,
        changes=service.changes,
        head=run.repo.head,
    )
    if not targets:
        raise ChangeResolutionError("no commits to show")

    for resolved in targets:
        print_resolution_note(resolved.resolution)

    ci = None
    meter = DownloadMeter(sys.stderr, label="Fetching CI logs")
    if settings.show_ci_content if args.ci is None else args.ci:
        ci = CiContentService.from_settings(
            cwd, settings, web_base=service.web_base, http=ci_http, refresh=service.refresh, progress=meter
        )
    snap = (
        local_stack_or_none(cwd, branch, settings=settings, head=run.repo.head)
        if any(t.is_local_commit for t in targets)
        else None
    )
    try:
        shown = load_shown_commits(
            targets,
            service=service,
            comments=args.comments,
            context_padding=_requested_context_padding(args),
            fetch_ci_pipelines=verbose_level >= 1,
            ci=ci,
            stack=snap,
        )
    finally:
        meter.close()

    if snap is not None and branch is not None:
        try:
            stack = resolve_stack_context(cwd, branch, settings=settings)
        except ChangeResolutionError:
            pass
        else:
            summary_highlighter = run_summary_highlighter(run, commits=list(snap.commits), project=stack.project)

    json_payloads: list[dict[str, object]] = []
    multi = len(targets) > 1

    for item in shown:
        resolved, commit, chains, ci_contents = item.target, item.commit, item.chains, item.ci_contents
        if out_fmt == "json":
            json_payloads.append(
                commit_json_payload(
                    commit,
                    is_local=resolved.is_local_commit,
                    resolution=resolved.resolution,
                    chains=chains,
                    ci_content=ci_contents,
                )
            )
        else:
            if out_fmt == "markdown":
                markdown = commit_markdown(
                    commit, chains=chains, ci_contents=ci_contents, comments=args.comments, blocked_by=item.blocked_by
                )
                for line in markdown:
                    print(line)
                print()
                continue
            if resolved is not targets[0]:
                print(color_text(_COMMIT_SEPARATOR, ANSI_YELLOW))
            if multi and not chains and not ci_contents:
                _emit_brief_commit(commit, comments=args.comments)
                continue
            _emit_human_commit(cwd, item, summary_highlighter=summary_highlighter, wrap_width=settings.wrap_width)

    if out_fmt == "json":
        if len(json_payloads) == 1:
            print(json.dumps(json_payloads[0], indent=2))
        else:
            print(json.dumps({"commits": json_payloads}, indent=2))

    # Shown what is known locally; now say, once, why the rest is not.
    if unavailable := next((item.gerrit_unavailable for item in shown if item.gerrit_unavailable), None):
        raise unavailable
    return int(ExitCode.OK)


if __name__ == "__main__":
    raise SystemExit(main())
