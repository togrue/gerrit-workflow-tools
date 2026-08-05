"""CLI for ``ger show``: commit(s) vs Gerrit (status + unresolved comments)."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from gerrit_workflow_tools.cli_common import (
    HELP_JSON,
    ExitCode,
    add_color_args,
    add_verbose_and_debug_log_args,
    init_cli_runtime,
    run_cli_command,
)
from gerrit_workflow_tools.cli_style import ANSI_DIM, color_text
from gerrit_workflow_tools.core.annotated_stack import annotate
from gerrit_workflow_tools.core.comment_chains import collect_unresolved_comment_chains
from gerrit_workflow_tools.core.gerrit.change_resolution import (
    ChangeResolutionError,
    format_resolution_note,
)
from gerrit_workflow_tools.core.gerrit.rest import GerritRest
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.gerrit_change_status import (
    CommentChain,
    LogCommit,
    gerrit_inline_comment_url,
)
from gerrit_workflow_tools.core.gerrit_show import resolve_show_targets
from gerrit_workflow_tools.core.git_run import git_out
from gerrit_workflow_tools.render.comments import (
    format_unresolved_section_human,
    format_unresolved_section_markdown,
)
from gerrit_workflow_tools.render.commit_row import attention_column, extra_detail_lines, oneline_body
from gerrit_workflow_tools.summary_highlight import SummaryHighlighter

logger = logging.getLogger(__name__)


def _print_resolution_note(resolution_note: str | None, *, use_color: bool) -> None:
    if not resolution_note:
        return
    text = color_text(resolution_note, ANSI_DIM) if use_color else resolution_note
    print(text, file=sys.stderr)


def _gerrit_rest_key(commit: LogCommit, resolution: object) -> str | None:
    selected = getattr(resolution, "selected", None)
    if selected is not None:
        return selected.triplet
    change_id = getattr(commit, "change_id", None)
    return change_id if isinstance(change_id, str) and change_id else None


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
    p.add_argument(
        "--full",
        action="store_true",
        help="Show full comment bodies without tail truncation (human format only).",
    )
    p.add_argument(
        "--comment-tail-lines",
        type=int,
        metavar="LINES",
        default=None,
        help=("Show only the last N lines of each comment body (positive integer; overrides config)."),
    )
    fmt = p.add_mutually_exclusive_group()
    fmt.add_argument(
        "--json",
        action="store_true",
        dest="json_",
        help=HELP_JSON,
    )
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


def _comment_json_payload(
    unresolved_chains: list[CommentChain],
    gerrit_url: str | None,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    comment_payload: list[dict[str, object]] = []
    chain_payload: list[dict[str, object]] = []
    for chain in unresolved_chains:
        chain_comments: list[dict[str, object]] = []
        for row_item in chain.comments:
            entry: dict[str, object] = {
                "path": row_item.path,
                "line": row_item.line,
                "message": row_item.message,
                "url": gerrit_inline_comment_url(gerrit_url, row_item.comment_id),
            }
            if row_item.author:
                entry["author"] = row_item.author
            if row_item.comment_id:
                entry["comment_id"] = row_item.comment_id
            chain_comments.append(entry)
            comment_payload.append(entry)
        chain_payload.append(
            {
                "path": chain.path,
                "line": chain.line,
                "url": gerrit_inline_comment_url(gerrit_url, chain.root_id),
                "comments": chain_comments,
            }
        )
    return comment_payload, chain_payload


def _commit_json_payload(
    commit: LogCommit,
    *,
    is_local: bool,
    resolution: object,
    unresolved_chains: list[CommentChain],
) -> dict[str, object]:
    comment_payload, chain_payload = _comment_json_payload(unresolved_chains, commit.gerrit_url)
    return {
        "sha": commit.sha if commit.sha else None,
        "change_id": commit.change_id,
        "summary": commit.summary,
        "pushed": commit.pushed,
        "patchset_status": commit.patchset_status,
        "verified": commit.verified,
        "code_review": commit.code_review,
        "comments_unresolved": commit.comments_unresolved,
        "ci_failures": commit.ci_failures,
        "gerrit_url": commit.gerrit_url,
        "submittable": commit.submittable,
        "attention_reasons": commit.attention_reasons,
        "comments": comment_payload,
        "comment_chains": chain_payload,
        "local_commit": is_local,
        "change_status": commit.change_status,
        "merged_equivalent": commit.merged_equivalent,
        "resolution": resolution.to_json_dict(),  # type: ignore[attr-defined]
    }


def _emit_human_commit(
    cwd: object,
    commit: LogCommit,
    *,
    is_local: bool,
    unresolved_chains: list[CommentChain],
    summary_highlighter: SummaryHighlighter,
    sibling_commits: list[LogCommit],
    tail_n: int,
    full: bool,
) -> None:
    if is_local and commit.sha:
        msg = git_out("show", "-s", "--no-patch", "--pretty=medium", commit.sha, cwd=cwd)
        print()
        print(msg.rstrip())

    ind = " " * 4
    print()
    if commit.gerrit_url:
        print(f"{ind}{color_text(commit.gerrit_url, ANSI_DIM)}")
    for d in extra_detail_lines(commit):
        print(f"{ind}{d}")
    attn_col = attention_column(sibling_commits, summary_highlighter=summary_highlighter)
    print(f"{ind}{oneline_body(commit, summary_highlighter=summary_highlighter, attention_col=attn_col)}")

    print()
    for line in format_unresolved_section_human(
        unresolved_chains,
        commit.gerrit_url,
        pushed=commit.pushed,
        tail_n=tail_n,
        full=full,
    ):
        print(line)


def _attention_summary(commit: LogCommit) -> str:
    if commit.attention_reasons:
        return "attention: " + ", ".join(commit.attention_reasons)
    if not commit.pushed:
        return "not on Gerrit"
    return "ok"


def _emit_markdown_commit(
    commit: LogCommit,
    *,
    unresolved_chains: list[CommentChain],
) -> None:
    short = commit.short_sha or (commit.sha[:8] if commit.sha else "????????")
    summary = commit.summary or ""
    print(f"## {short} — {summary}")
    print(f"- {_attention_summary(commit)}")
    if commit.gerrit_url:
        print(f"- Gerrit: {commit.gerrit_url}")
    print()
    for line in format_unresolved_section_markdown(
        unresolved_chains,
        commit.gerrit_url,
        pushed=commit.pushed,
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
    cwd, settings, summary_highlighter = init_cli_runtime(debug_log=args.debug_log, color=args.color)
    use_color = args.color != "never"
    out_fmt = _output_format(args)

    if args.comment_tail_lines is not None and args.comment_tail_lines < 1:
        print("error: --comment-tail-lines must be a positive integer", file=sys.stderr)
        return int(ExitCode.USAGE)

    tail_n = args.comment_tail_lines
    if tail_n is None:
        tail_n = settings.show_comment_tail_lines

    # Markdown / JSON ignore human tail truncation.
    full_bodies = out_fmt != "human" or args.full

    service = GerritService.from_cwd(cwd, settings=settings, rest=gerrit)
    targets = resolve_show_targets(
        cwd,
        list(args.revs),
        service.rest,
        settings=settings,
        stack=args.stack,
    )
    if not targets:
        raise ChangeResolutionError("no commits to show")

    for resolved in targets:
        _print_resolution_note(format_resolution_note(resolved.resolution), use_color=use_color)

    rows = [t.row for t in targets]
    commits = annotate(rows, service=service, cwd=cwd)
    if len(commits) != len(targets):
        raise ChangeResolutionError("commit annotation mismatch")

    any_attention = False
    json_payloads: list[dict[str, object]] = []

    for resolved, commit in zip(targets, commits, strict=True):
        attention = commit.attention_reasons
        if attention:
            any_attention = True

        rest_key = _gerrit_rest_key(commit, resolved.resolution)
        file_map = service.comments.get_file_map(rest_key) if (commit.pushed and rest_key) else {}
        unresolved_chains = collect_unresolved_comment_chains(file_map)

        if out_fmt == "json":
            json_payloads.append(
                _commit_json_payload(
                    commit,
                    is_local=resolved.is_local_commit,
                    resolution=resolved.resolution,
                    unresolved_chains=unresolved_chains,
                )
            )
        elif out_fmt == "markdown":
            _emit_markdown_commit(commit, unresolved_chains=unresolved_chains)
            print()
        else:
            _emit_human_commit(
                cwd,
                commit,
                is_local=resolved.is_local_commit,
                unresolved_chains=unresolved_chains,
                summary_highlighter=summary_highlighter,
                sibling_commits=commits,
                tail_n=tail_n,
                full=full_bodies,
            )

    if out_fmt == "json":
        if len(json_payloads) == 1:
            print(json.dumps(json_payloads[0], indent=2))
        else:
            print(json.dumps({"commits": json_payloads}, indent=2))

    return int(ExitCode.ATTENTION) if any_attention else int(ExitCode.OK)


if __name__ == "__main__":
    raise SystemExit(main())
