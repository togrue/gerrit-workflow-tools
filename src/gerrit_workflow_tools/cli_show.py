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
from gerrit_workflow_tools.cli_style import ANSI_DIM, ANSI_YELLOW, color_short_sha, color_text, format_link
from gerrit_workflow_tools.core.annotated_stack import annotate
from gerrit_workflow_tools.core.comment_chains import collect_unresolved_comment_chains
from gerrit_workflow_tools.core.gerrit.change_resolution import (
    ChangeResolutionError,
    format_resolution_note,
    resolve_stack_context,
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
from gerrit_workflow_tools.core.git_state import resolve_working_branch
from gerrit_workflow_tools.core.ready_strategy import ReadyCommitRow
from gerrit_workflow_tools.core.stack import commits_in_range, merge_base_with_target
from gerrit_workflow_tools.render.comments import (
    format_unresolved_section_human,
    format_unresolved_section_markdown,
)
from gerrit_workflow_tools.render.commit_row import (
    attention_suffix,
    continuation_lines,
    fmt_code_review,
    fmt_comments,
    fmt_patchset_column,
    fmt_verified,
)
from gerrit_workflow_tools.summary_highlight import SummaryHighlighter, build_summary_highlighter

_COMMIT_SEPARATOR = "═" * 64

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
        "--branch",
        metavar="NAME",
        default=None,
        help="Use the specified local branch for stack/upstream context (default: working branch).",
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
        "ci_links": [
            {"label": link.label, "url": link.url, "source": link.source} for link in commit.ci_links
        ],
        "ci_pipelines": [
            {
                "label": pipe.label,
                "state": pipe.state,
                **({"url": pipe.url} if pipe.url else {}),
            }
            for pipe in commit.ci_pipelines
        ],
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


def _show_headline(commit: LogCommit) -> str:
    """``commit <sha> <status cols>  # <attention>`` — same tokens as ``ger log``."""
    sha = color_short_sha(commit.short_sha)
    push = fmt_patchset_column(commit)
    if commit.pushed:
        verified = fmt_verified(commit.verified)
        cr = fmt_code_review(commit.code_review)
        comments = fmt_comments(commit.comments_unresolved)
    else:
        verified = "   "
        cr = "    "
        comments = "   "
    base = f"commit {sha} {push} {verified} {cr} {comments}"
    suffix = attention_suffix(commit)
    if suffix:
        return f"{base}  {suffix}"
    return base


def _local_commit_meta(cwd: object, sha: str) -> tuple[str, str, str]:
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
    if email:
        author_line = f"{author} <{email}>"
    else:
        author_line = author
    return author_line, date, body


def _emit_human_commit(
    cwd: object,
    commit: LogCommit,
    *,
    is_local: bool,
    unresolved_chains: list[CommentChain],
    summary_highlighter: SummaryHighlighter,
) -> None:
    print(_show_headline(commit))

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
        print()
        body_lines = body.splitlines()
        if body_lines and summary_highlighter is not None:
            body_lines[0] = summary_highlighter.highlight(body_lines[0], sha=commit.sha)
        for ln in body_lines:
            print(f"    {ln}")

    comment_lines = format_unresolved_section_human(
        unresolved_chains,
        commit.gerrit_url,
        pushed=commit.pushed,
    )
    if comment_lines:
        print()
        for line in comment_lines:
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
    cwd, settings, summary_highlighter = init_cli_runtime(
        debug_log=args.debug_log, color=args.color, hyperlinks=args.hyperlinks
    )
    use_color = args.color != "never"
    out_fmt = _output_format(args)
    verbose_level = int(args.verbose)
    branch = args.branch or resolve_working_branch(cwd, settings=settings)

    service = GerritService.from_cwd(cwd, settings=settings, rest=gerrit)
    targets = resolve_show_targets(
        cwd,
        list(args.revs),
        service.rest,
        settings=settings,
        stack=args.stack,
        branch=branch,
    )
    if not targets:
        raise ChangeResolutionError("no commits to show")

    for resolved in targets:
        _print_resolution_note(format_resolution_note(resolved.resolution), use_color=use_color)

    rows = [t.row for t in targets]
    commits = annotate(rows, service=service, cwd=cwd, fetch_ci_pipelines=verbose_level >= 1)
    if len(commits) != len(targets):
        raise ChangeResolutionError("commit annotation mismatch")

    if any(t.is_local_commit for t in targets):
        stack = resolve_stack_context(cwd, branch, settings=settings)
        _fork, _display, target_tip = merge_base_with_target(cwd, branch, settings=settings)
        stack_rows = commits_in_range(cwd, f"{target_tip}..HEAD", first_parent=True)
        summary_highlighter = build_summary_highlighter(
            settings,
            cwd=cwd,
            commits=[
                ReadyCommitRow(sha=r.sha, short_sha=r.short_sha, subject=r.subject, change_id=r.change_id)
                for r in stack_rows
            ],
            project=stack.project,
            web_base=settings.gerrit_web_url,
        )

    any_attention = False
    json_payloads: list[dict[str, object]] = []
    multi = len(targets) > 1
    human_shown = 0

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
            # Multi-target: only expand commits that have unresolved comment chains.
            if multi and not unresolved_chains:
                continue
            if human_shown:
                print(color_text(_COMMIT_SEPARATOR, ANSI_YELLOW))
            _emit_human_commit(
                cwd,
                commit,
                is_local=resolved.is_local_commit,
                unresolved_chains=unresolved_chains,
                summary_highlighter=summary_highlighter,
            )
            human_shown += 1

    if out_fmt == "human" and multi and human_shown == 0:
        print(color_text("(no unresolved comments)", ANSI_DIM))

    if out_fmt == "json":
        if len(json_payloads) == 1:
            print(json.dumps(json_payloads[0], indent=2))
        else:
            print(json.dumps({"commits": json_payloads}, indent=2))

    return int(ExitCode.ATTENTION) if any_attention else int(ExitCode.OK)


if __name__ == "__main__":
    raise SystemExit(main())
