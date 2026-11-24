"""CLI for ``ger show``: commits vs Gerrit (status + inline comments)."""

from __future__ import annotations

import argparse
import json
import sys

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
from gerrit_workflow_tools.core.ci_content_service import CiContentService
from gerrit_workflow_tools.core.ci_http import CiHttp
from gerrit_workflow_tools.core.comment_chains import COMMENT_SELECTIONS
from gerrit_workflow_tools.core.gerrit.change_resolution import ChangeResolutionError
from gerrit_workflow_tools.core.gerrit.rest import GerritRest
from gerrit_workflow_tools.core.gerrit.stack_context import resolve_stack_context
from gerrit_workflow_tools.core.gerrit_show import (
    ShowCommitResolution,
    ShownCommit,
    load_shown_commits,
    resolve_show_targets,
)
from gerrit_workflow_tools.core.run_context import RunContext
from gerrit_workflow_tools.core.stack import StackSnapshot, read_commit_meta
from gerrit_workflow_tools.render.download_meter import DownloadMeter
from gerrit_workflow_tools.render.show_json import commit_json_payload
from gerrit_workflow_tools.render.show_markdown import commit_markdown
from gerrit_workflow_tools.render.show_text import brief_commit_lines, commit_lines, commit_separator
from gerrit_workflow_tools.summary_highlight import SummaryHighlighter, run_summary_highlighter
from gerrit_workflow_tools.upstream_interactive import require_branch_upstream


DEFAULT_SHOW_CONTEXT_PADDING = 2


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
        description="Show commits and their Gerrit status (labels, comments, CI).",
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


def main(argv: list[str] | None = None, *, gerrit: GerritRest | None = None, ci_http: CiHttp | None = None) -> int:
    """Resolve the revisions and print human, Markdown, or JSON Gerrit status details."""
    return run_cli_command(lambda: _run(argv, gerrit=gerrit, ci_http=ci_http))


def _run(argv: list[str] | None, *, gerrit: GerritRest | None, ci_http: CiHttp | None) -> int:
    p = _build_parser()
    args = p.parse_args(argv)
    run = init_cli_runtime(args, gerrit=gerrit)
    cwd, settings = run.cwd, run.settings
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
    targets = resolve_show_targets(
        cwd,
        revs_or_code,
        run.gerrit.rest,
        settings=settings,
        stack=args.stack,
        branch=branch,
        changes=run.gerrit.changes,
        head=run.repo.head,
    )
    if not targets:
        raise ChangeResolutionError("no commits to show")
    for resolved in targets:
        print_resolution_note(resolved.resolution)
    snap = (
        local_stack_or_none(cwd, branch, settings=settings, head=run.repo.head)
        if any(t.is_local_commit for t in targets)
        else None
    )
    shown = _load(run, args, targets, snap, ci_http=ci_http)
    _print_shown(run, args, shown, multi=len(targets) > 1, highlighter=_highlighter(run, snap, branch))
    # Shown what is known locally; now say, once, why the rest is not.
    if unavailable := next((item.gerrit_unavailable for item in shown if item.gerrit_unavailable), None):
        raise unavailable
    return int(ExitCode.OK)


def _load(
    run: RunContext,
    args: argparse.Namespace,
    targets: list[ShowCommitResolution],
    snap: StackSnapshot | None,
    *,
    ci_http: CiHttp | None,
) -> list[ShownCommit]:
    """What there is to show about each target, CI content too when asked (with the download meter)."""
    service, settings = run.gerrit, run.settings
    ci = None
    meter = DownloadMeter(sys.stderr, label="Fetching CI logs")
    if settings.show_ci_content if args.ci is None else args.ci:
        ci = CiContentService.from_settings(
            run.cwd, settings, web_base=service.web_base, http=ci_http, refresh=service.refresh, progress=meter
        )
    try:
        return load_shown_commits(
            targets,
            service=service,
            comments=args.comments,
            context_padding=_requested_context_padding(args),
            fetch_ci_pipelines=int(args.verbose) >= 1,
            ci=ci,
            stack=snap,
        )
    finally:
        meter.close()


def _highlighter(run: RunContext, snap: StackSnapshot | None, branch: str | None) -> SummaryHighlighter:
    """The subject highlighter: the branch's stack's own when the targets are in it."""
    if snap is not None and branch is not None:
        try:
            stack = resolve_stack_context(run.cwd, branch, settings=run.settings)
        except ChangeResolutionError:
            pass
        else:
            return run_summary_highlighter(run, commits=list(snap.commits), project=stack.project)
    return run_summary_highlighter(run)


def _print_shown(
    run: RunContext, args: argparse.Namespace, shown: list[ShownCommit], *, multi: bool, highlighter: SummaryHighlighter
) -> None:
    """Each shown commit in the output format asked for: JSON once at the end, else commit by commit."""
    out_fmt = output_format(args)
    if out_fmt == "json":
        payloads = [
            commit_json_payload(
                item.commit,
                is_local=item.target.is_local_commit,
                resolution=item.target.resolution,
                chains=item.chains,
                ci_content=item.ci_contents,
            )
            for item in shown
        ]
        print(json.dumps(payloads[0] if len(payloads) == 1 else {"commits": payloads}, indent=2))
        return
    for index, item in enumerate(shown):
        commit = item.commit
        if out_fmt == "markdown":
            markdown = commit_markdown(
                commit,
                chains=item.chains,
                ci_contents=item.ci_contents,
                comments=args.comments,
                blocked_by=item.blocked_by,
            )
            print("\n".join([*markdown, ""]))
            continue
        if index:
            print(commit_separator())
        if multi and not item.chains and not item.ci_contents:
            lines = brief_commit_lines(commit, comments=args.comments)
        else:
            meta = read_commit_meta(run.cwd, commit.sha) if item.target.is_local_commit and commit.sha else None
            lines = commit_lines(item, meta, summary_highlighter=highlighter, wrap_width=run.settings.wrap_width)
        print("\n".join(lines))


if __name__ == "__main__":
    raise SystemExit(main())
