"""CLI for ``ger ci``: **CI findings** behind each failed **CI link** (spec: ``docu/spec/commands/ci.md``)."""

from __future__ import annotations

import argparse
import json

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
from gerrit_workflow_tools.cli_style import ANSI_DIM, ANSI_RED, color_text
from gerrit_workflow_tools.core.annotated_stack import annotate
from gerrit_workflow_tools.core.ci_content import BodyMode, CiContent, CiContentError
from gerrit_workflow_tools.core.ci_content_service import CiContentService
from gerrit_workflow_tools.core.ci_http import CiHttp
from gerrit_workflow_tools.core.gerrit.change_resolution import ChangeResolutionError
from gerrit_workflow_tools.core.gerrit.rest import GerritRest
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.gerrit_change_status import LogCommit
from gerrit_workflow_tools.core.gerrit_show import resolve_show_targets
from gerrit_workflow_tools.core.git_state import resolve_working_branch
from gerrit_workflow_tools.core.upstream_interactive import require_branch_upstream
from gerrit_workflow_tools.render.ci_content import (
    ci_content_json,
    format_ci_section_human,
    format_ci_section_markdown,
)
from gerrit_workflow_tools.render.commit_row import show_headline


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the command-line parser for ``ger ci``."""
    p = argparse.ArgumentParser(
        prog="ger ci",
        description="Fetch the CI logs behind failed CI links and list the findings (why CI failed).",
    )
    p.add_argument(
        "revs",
        nargs="*",
        metavar="CHANGEISH",
        help="Changeish, or A..B / A...B range with changeish endpoints (default: HEAD unless --stack).",
    )
    p.add_argument("--stack", action="store_true", help="Include all commits in the local stack (upstream_tip..HEAD).")
    p.add_argument(
        "--branch",
        metavar="NAME",
        default=None,
        help="Use the specified local branch for stack/upstream context (default: working branch).",
    )
    p.add_argument(
        "--full",
        action="store_true",
        help="Print each log in full after its findings (for searching the raw log).",
    )
    p.add_argument(
        "--warnings",
        action="store_true",
        help="List the warnings below the errors, not just their count (JSON always has them).",
    )
    add_output_format_args(p)
    add_color_args(p)
    add_verbose_and_debug_log_args(
        p,
        verbose_help="Also print the last lines of each log after its findings.",
        debug_log_help="Log Gerrit resolution and CI fetches to stderr.",
    )
    return p


def _body_mode(args: argparse.Namespace) -> BodyMode:
    if args.full:
        return "full"
    return "tail" if args.verbose else "none"


def main(
    argv: list[str] | None = None,
    *,
    gerrit: GerritRest | None = None,
    ci_http: CiHttp | None = None,
) -> int:
    """Print **CI findings** for each target's failed **CI links**; exit 11 if any link cannot be fetched."""
    return run_cli_command(lambda: _run(argv, gerrit=gerrit, ci_http=ci_http))


def _commit_json(commit: LogCommit, contents: list[CiContent], *, include_body: bool) -> dict[str, object]:
    return {
        "sha": commit.sha or None,
        "change_id": commit.change_id,
        "summary": commit.summary,
        "pushed": commit.pushed,
        "verified": commit.verified,
        "gerrit_url": commit.gerrit_url,
        "ci_failures": commit.ci_failures,
        "ci_content": [ci_content_json(c, include_body=include_body) for c in contents],
    }


def _no_links_note(commit: LogCommit) -> str:
    if not commit.pushed:
        return "(not on Gerrit)"
    if commit.ci_failures:
        return "(CI failed, but no CI link was found: " + ", ".join(commit.ci_failures) + ")"
    return "(no failed CI links)"


def _emit_human(commit: LogCommit, contents: list[CiContent], *, warnings: bool) -> None:
    print(show_headline(commit))
    if not contents:
        print(color_text(f"    {_no_links_note(commit)}", ANSI_DIM))
        return
    for line in format_ci_section_human(contents, warnings=warnings):
        print(color_text(line, ANSI_RED) if line.lstrip().startswith("error: ") else line)


def _emit_markdown(commit: LogCommit, contents: list[CiContent], *, warnings: bool) -> None:
    short = commit.short_sha or (commit.sha[:8] if commit.sha else "????????")
    print(f"## {short} — {commit.summary or ''}")
    if commit.gerrit_url:
        print(f"- Gerrit: {commit.gerrit_url}")
    print()
    if not contents:
        print(f"- {_no_links_note(commit)}")
        print()
        return
    for line in format_ci_section_markdown(contents, warnings=warnings):
        print(line)


def _run(argv: list[str] | None, *, gerrit: GerritRest | None, ci_http: CiHttp | None) -> int:
    p = _build_parser()
    args = p.parse_args(argv)
    cwd, settings, _summary_highlighter = init_cli_runtime(args)
    out_fmt = output_format(args)
    branch = args.branch or resolve_working_branch(cwd, settings=settings)
    if args.stack and branch is not None and not require_branch_upstream(cwd, branch, settings=settings):
        return int(ExitCode.ATTENTION)

    service = GerritService.from_cwd(cwd, settings=settings, rest=gerrit)
    targets = resolve_show_targets(
        cwd, args.revs, service.rest, settings=settings, stack=args.stack, branch=branch, changes=service.changes
    )
    if not targets:
        raise ChangeResolutionError("no commits to inspect")
    for resolved in targets:
        print_resolution_note(resolved.resolution)
    commits = annotate([t.row for t in targets], service=service, cwd=cwd).require_gerrit()

    ci = CiContentService.from_settings(cwd, settings, web_base=service.web_base, http=ci_http, refresh=service.refresh)
    body = _body_mode(args)
    per_commit = [(c, ci.fetch(c.ci_links, body=body, commit=c.sha or None)) for c in commits]

    if out_fmt == "json":
        payload = [_commit_json(c, contents, include_body=body != "none") for c, contents in per_commit]
        print(json.dumps({"commits": payload}, indent=2))
    else:
        for index, (commit, contents) in enumerate(per_commit):
            if index and out_fmt == "human":
                print()
            (_emit_markdown if out_fmt == "markdown" else _emit_human)(commit, contents, warnings=args.warnings)

    failed = [c for _commit, contents in per_commit for c in contents if c.failed]
    if failed:
        raise CiContentError(
            f"could not fetch CI content for {len(failed)} link(s): "
            + "; ".join(f"{c.link.url}: {c.provenance.error}" for c in failed)
        )
    needs_attention = any(contents or commit.ci_failures for commit, contents in per_commit)
    return int(ExitCode.ATTENTION) if needs_attention else int(ExitCode.OK)


if __name__ == "__main__":
    raise SystemExit(main())
