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
    add_json_arg,
    add_verbose_and_debug_log_args,
    init_cli_runtime,
    print_resolution_note,
    run_cli_command,
)
from gerrit_workflow_tools.cli_style import ANSI_DIM, ANSI_YELLOW, color_text, format_link
from gerrit_workflow_tools.core.annotated_stack import annotate
from gerrit_workflow_tools.core.comment_chains import COMMENT_SELECTIONS, CommentSelection
from gerrit_workflow_tools.core.gerrit.change_resolution import ChangeResolutionError
from gerrit_workflow_tools.core.gerrit.rest import GerritRest
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.gerrit.stack_context import resolve_stack_context
from gerrit_workflow_tools.core.gerrit_change_status import CommentChain, LogCommit
from gerrit_workflow_tools.core.gerrit_show import resolve_show_targets
from gerrit_workflow_tools.core.git_run import git_out
from gerrit_workflow_tools.core.git_state import resolve_working_branch
from gerrit_workflow_tools.core.stack import get_stack_snapshot
from gerrit_workflow_tools.core.upstream_interactive import branch_has_upstream, require_branch_upstream
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


def _gerrit_rest_key(commit: LogCommit, resolution: object) -> str | None:
    selected = getattr(resolution, "selected", None)
    return selected.triplet if selected is not None else commit.change_id


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
    fmt = p.add_mutually_exclusive_group()
    add_json_arg(fmt)
    fmt.add_argument(
        "--format",
        choices=("human", "markdown"),
        default=None,
        dest="format_",
        help="Output format (default: human). 'markdown' is suited for AI review.",
    )
    fmt.add_argument(
        "--ai",
        action="store_true",
        help="Alias for --format markdown.",
    )
    add_color_args(p)
    add_verbose_and_debug_log_args(
        p,
        debug_log_help="Log Gerrit resolution to stderr.",
    )
    return p


def _output_format(args: argparse.Namespace) -> str:
    if args.json_:
        return "json"
    if args.ai or args.format_ == "markdown":
        return "markdown"
    return "human"


def _local_commit_meta(cwd: Path | str | None, sha: str) -> tuple[str, str, str]:
    """Return ``(author_line, date, message_body)`` from git for *sha*."""
    raw = git_out(
        "show",
        "-s",
        "--no-patch",
        "--format=%an%n%ae%n%ad%n%B",
        sha,
        cwd=cwd,
    )
    lines = raw.splitlines()
    while lines and lines[-1] == "":
        lines.pop()
    author = lines[0] if lines else ""
    email = lines[1] if len(lines) > 1 else ""
    date = lines[2] if len(lines) > 2 else ""
    body = "\n".join(lines[3:]).rstrip("\n") if len(lines) > 3 else ""
    author_line = f"{author} <{email}>" if email else author
    return author_line, date, body


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
    summary_highlighter: SummaryHighlighter,
    wrap_width: int,
) -> None:
    print(show_headline(commit))
    if commit.attention_reasons:
        print(color_text(_attention_summary(commit), ANSI_DIM))

    body = ""
    if is_local and commit.sha:
        author_line, date, body = _local_commit_meta(cwd, commit.sha)
        meta = f"Author: {author_line}"
        if date:
            meta = f"{meta} [{date}]"
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


def main(argv: list[str] | None = None, *, gerrit: GerritRest | None = None) -> int:
    """Resolve revision(s) and print human, Markdown, or JSON Gerrit status details."""
    return run_cli_command(lambda: _run(argv, gerrit=gerrit))


def _run(  # pylint: disable=too-many-branches,too-many-locals,too-many-statements
    argv: list[str] | None,
    *,
    gerrit: GerritRest | None,
) -> int:
    p = _build_parser()
    args = p.parse_args(argv)
    cwd, settings, summary_highlighter = init_cli_runtime(args)
    out_fmt = _output_format(args)
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

    for resolved, commit in zip(targets, commits, strict=True):
        attention = commit.attention_reasons
        if attention:
            any_attention = True

        rest_key = _gerrit_rest_key(commit, resolved.resolution)
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

        if out_fmt == "json":
            json_payloads.append(
                commit_json_payload(
                    commit,
                    is_local=resolved.is_local_commit,
                    resolution=resolved.resolution,
                    chains=chains,
                )
            )
        else:
            if out_fmt == "markdown":
                _emit_markdown_commit(commit, chains=chains, comments=args.comments)
                print()
                continue
            if resolved is not targets[0]:
                print(color_text(_COMMIT_SEPARATOR, ANSI_YELLOW))
            if multi and not chains:
                _emit_brief_commit(commit, comments=args.comments)
                continue
            _emit_human_commit(
                cwd,
                commit,
                is_local=resolved.is_local_commit,
                chains=chains,
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
