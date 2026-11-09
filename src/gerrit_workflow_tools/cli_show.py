"""CLI for ``ger show``: commit(s) vs Gerrit (status + inline comments)."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from gerrit_workflow_tools.cli_attention import add_attention_query_args, show_revs_with_attention
from gerrit_workflow_tools.cli_common import (
    ExitCode,
    add_color_args,
    add_output_format_args,
    add_verbose_and_debug_log_args,
    init_cli_runtime,
    output_format,
    print_resolution_note,
    run_cli_command,
)
from gerrit_workflow_tools.cli_style import ANSI_DIM, ANSI_YELLOW, color_text, format_link
from gerrit_workflow_tools.core.annotated_stack import annotate
from gerrit_workflow_tools.core.ci_content import CiContent
from gerrit_workflow_tools.core.ci_content_service import CiContentService
from gerrit_workflow_tools.core.ci_http import CiHttp
from gerrit_workflow_tools.core.comment_chains import COMMENT_SELECTIONS, CommentSelection, merge_chains
from gerrit_workflow_tools.core.gerrit.change_resolution import ChangeResolutionError
from gerrit_workflow_tools.core.gerrit.rest import GerritRest
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.gerrit.stack_context import resolve_stack_context
from gerrit_workflow_tools.core.gerrit_change_status import CommentChain, LogCommit
from gerrit_workflow_tools.core.gerrit_show import gerrit_rest_key, resolve_show_targets
from gerrit_workflow_tools.core.git_state import resolve_working_branch
from gerrit_workflow_tools.core.local_comments import local_chains_of
from gerrit_workflow_tools.core.stack import get_stack_snapshot, read_commit_meta
from gerrit_workflow_tools.core.upstream_interactive import branch_has_upstream, require_branch_upstream
from gerrit_workflow_tools.render.ci_content import format_ci_section_human, format_ci_section_markdown
from gerrit_workflow_tools.render.comments import (
    empty_comments_message,
    format_unresolved_section_human,
    format_unresolved_section_markdown,
)
from gerrit_workflow_tools.render.commit_row import continuation_lines, show_headline
from gerrit_workflow_tools.render.show_json import commit_json_payload
from gerrit_workflow_tools.render.wrap import wrap_lines
from gerrit_workflow_tools.summary_highlight import SummaryHighlighter, build_summary_highlighter


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
    )
    p.add_argument(
        "revs",
        nargs="*",
        metavar="REV",
        help=(
            "Changeish, or A..B / A...B range with changeish endpoints "
            "(default: HEAD when neither REV nor --stack is given)."
        ),
    )
    p.add_argument(
        "--stack",
        action="store_true",
        help="Include all commits in the local stack (upstream_tip..HEAD).",
    )
    add_attention_query_args(p)
    p.add_argument(
        "--branch",
        metavar="NAME",
        default=None,
        help="Use the specified local branch for stack/upstream context (default: working branch).",
    )
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
    p.add_argument(
        "--ci",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Attach CI findings from failed CI links (fetches CI logs; default: ger.showCiContent, off).",
    )
    add_output_format_args(p)
    add_color_args(p)
    add_verbose_and_debug_log_args(
        p,
        debug_log_help="Log Gerrit resolution to stderr.",
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
    commit: LogCommit,
    *,
    is_local: bool,
    chains: list[CommentChain],
    ci_contents: list[CiContent] | None,
    summary_highlighter: SummaryHighlighter,
    wrap_width: int,
) -> None:
    print(show_headline(commit))
    if commit.attention_reasons:
        print(color_text(_attention_summary(commit), ANSI_DIM))

    body = ""
    if is_local and commit.sha:
        meta_row = read_commit_meta(cwd, commit.sha)
        body = meta_row.body
        meta = f"Author: {meta_row.author_line}"
        if meta_row.date:
            meta = f"{meta} [{meta_row.date}]"
        print(meta)

    if commit.gerrit_url:
        print(f"{color_text('url:', ANSI_DIM)} {color_text(format_link(commit.gerrit_url), ANSI_YELLOW)}")
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
        width=wrap_width,
    )
    if comment_lines:
        print()
        for line in comment_lines:
            print(line)


def _emit_brief_commit(commit: LogCommit, *, comments: CommentSelection) -> None:
    """One commit of a multi-commit view that has no comments to expand."""
    print(show_headline(commit))
    print(f"    {commit.summary}")
    print(color_text(empty_comments_message(comments), ANSI_DIM))


def _attention_summary(commit: LogCommit) -> str:
    if commit.attention_reasons:
        return "attention: " + ", ".join(commit.attention_reasons)
    if not commit.pushed:
        return "not on Gerrit"
    return "ok"


def _emit_markdown_commit(
    commit: LogCommit,
    *,
    chains: list[CommentChain],
    ci_contents: list[CiContent] | None,
    comments: CommentSelection,
) -> None:
    short = commit.short_sha or (commit.sha[:8] if commit.sha else "????????")
    summary = commit.summary or ""
    print(f"## {short} — {summary}")
    print(f"- {_attention_summary(commit)}")
    if commit.gerrit_url:
        print(f"- Gerrit: {commit.gerrit_url}")
    print()
    for line in format_unresolved_section_markdown(
        chains,
        commit.gerrit_url,
        pushed=commit.pushed,
        comments=comments,
    ):
        print(line)
    if ci_contents:
        print()
        for line in format_ci_section_markdown(ci_contents):
            print(line)


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
    cwd, settings, summary_highlighter = init_cli_runtime(args)
    out_fmt = output_format(args)
    verbose_level = int(args.verbose)
    branch = args.branch or resolve_working_branch(cwd, settings=settings)

    if (
        (args.stack or args.first_attention or args.next_attention)
        and branch is not None
        and not require_branch_upstream(cwd, branch, settings=settings)
    ):
        return int(ExitCode.ATTENTION)

    revs_or_code = show_revs_with_attention(p, args, cwd=cwd, settings=settings, branch=branch, gerrit=gerrit)
    if isinstance(revs_or_code, int):
        return revs_or_code

    service = GerritService.from_cwd(cwd, settings=settings, rest=gerrit)
    targets = resolve_show_targets(
        cwd,
        revs_or_code,
        service.rest,
        settings=settings,
        stack=args.stack,
        branch=branch,
    )
    if not targets:
        raise ChangeResolutionError("no commits to show")

    for resolved in targets:
        print_resolution_note(resolved.resolution)

    rows = [t.row for t in targets]
    commits = annotate(rows, service=service, cwd=cwd, fetch_ci_pipelines=verbose_level >= 1)
    if len(commits) != len(targets):
        raise ChangeResolutionError("commit annotation mismatch")
    ci_by_commit: list[list[CiContent] | None] = [None] * len(commits)
    if settings.show_ci_content if args.ci is None else args.ci:
        ci = CiContentService.from_settings(
            cwd, settings, web_base=service.web_base, http=ci_http, refresh=service.refresh
        )
        ci_by_commit = [ci.fetch(c.ci_links) for c in commits]  # soft: a failure stays on its result

    if any(t.is_local_commit for t in targets) and branch is not None and branch_has_upstream(cwd, branch):
        try:
            stack = resolve_stack_context(cwd, branch, settings=settings)
        except ChangeResolutionError:
            pass
        else:
            snap = get_stack_snapshot(cwd, branch, first_parent=True, settings=settings)
            summary_highlighter = build_summary_highlighter(
                settings,
                cwd=cwd,
                commits=list(snap.commits),
                project=stack.project,
                web_base=settings.gerrit_web_url,
            )

    any_attention = False
    json_payloads: list[dict[str, object]] = []
    multi = len(targets) > 1

    for resolved, commit, ci_contents in zip(targets, commits, ci_by_commit, strict=True):
        attention = commit.attention_reasons
        if attention:
            any_attention = True

        rest_key = gerrit_rest_key(commit, resolved.resolution)
        chains = (
            service.comments.get_chains(
                rest_key,
                change_updated=commit.freshness,
                context_padding=_requested_context_padding(args),
                comments=args.comments,
            )
            if (commit.pushed and rest_key)
            else []
        )
        if rest_key:
            local = local_chains_of(service, rest_key, commit.sha or None, comments=args.comments)
            chains = merge_chains(chains, local)

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
                _emit_markdown_commit(commit, chains=chains, ci_contents=ci_contents, comments=args.comments)
                print()
                continue
            if resolved is not targets[0]:
                print(color_text(_COMMIT_SEPARATOR, ANSI_YELLOW))
            if multi and not chains and not ci_contents:
                _emit_brief_commit(commit, comments=args.comments)
                continue
            _emit_human_commit(
                cwd,
                commit,
                is_local=resolved.is_local_commit,
                chains=chains,
                ci_contents=ci_contents,
                summary_highlighter=summary_highlighter,
                wrap_width=settings.wrap_width,
            )

    if out_fmt == "json":
        if len(json_payloads) == 1:
            print(json.dumps(json_payloads[0], indent=2))
        else:
            print(json.dumps({"commits": json_payloads}, indent=2))

    return int(ExitCode.ATTENTION) if any_attention else int(ExitCode.OK)


if __name__ == "__main__":
    raise SystemExit(main())
