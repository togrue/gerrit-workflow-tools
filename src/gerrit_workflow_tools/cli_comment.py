"""CLI for ``ger comment``: start a comment chain on a line, a range or a file of a change."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from gerrit_workflow_tools.cli_common import (
    ExitCode,
    add_color_args,
    add_json_arg,
    add_verbose_and_debug_log_args,
    init_cli_runtime,
    read_message,
    run_cli_command,
)
from gerrit_workflow_tools.core.comment_anchor import (
    CommentLocation,
    LocationError,
    close_unchecked,
    file_lines,
    fit_to_file,
    parse_location,
    repository_path,
)
from gerrit_workflow_tools.core.comment_target import CommentTarget, resolve_comment_target, target_for_commit
from gerrit_workflow_tools.core.gerrit.rest import GerritRest
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.gerrit.stack_context import ChangeResolutionError
from gerrit_workflow_tools.core.gerrit_change_status import gerrit_inline_comment_url
from gerrit_workflow_tools.core.git_state import resolve_working_branch
from gerrit_workflow_tools.core.review_checkout import load_state
from gerrit_workflow_tools.render.comments import first_line


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the command-line parser for ``ger comment``."""
    p = argparse.ArgumentParser(
        prog="ger comment",
        usage=(
            "ger comment [options] LOCATION (-m MSG | -F FILE)\n"
            "       ger comment --discard ID [--change CHANGEISH] [--json]"
        ),
        description=(
            "Start a comment chain on a file, line or range. On a patchset Gerrit has, it is a draft "
            "there, visible only to you until published."
        ),
    )
    p.add_argument(
        "location",
        nargs="?",
        metavar="LOCATION",
        help="PATH, PATH:LINE, PATH:START-END or PATH:LINE.COL-LINE.COL (lines and columns from 1).",
    )
    p.add_argument(
        "--change",
        metavar="CHANGEISH",
        default=None,
        help="Comment on this change (default: the change under review here, else the commit at HEAD).",
    )
    p.add_argument("--branch", metavar="NAME", default=None, help="Local branch whose stack to resolve in.")
    text = p.add_argument_group("message")
    text.add_argument("-m", "--message", action="append", default=None, help="Message; repeat for paragraphs.")
    text.add_argument("-F", "--file", metavar="FILE", default=None, help="Read the message from FILE ('-': stdin).")
    p.add_argument("--resolved", action="store_true", help="Start the chain resolved (an FYI).")
    p.add_argument("--discard", metavar="ID", default=None, help="Delete your draft with this id instead.")
    add_json_arg(p)
    add_color_args(p)
    add_verbose_and_debug_log_args(p, debug_log_help="Log Gerrit calls to stderr.")
    return p


def _check_args(p: argparse.ArgumentParser, args: argparse.Namespace) -> str:
    """The message (empty with ``--discard``). Exits 2 on conflicting or missing arguments."""
    given = args.message is not None or args.file is not None
    if args.discard is not None:
        if args.location or given or args.resolved:
            p.error("--discard takes only an ID (and --change)")
        return ""
    if not args.location:
        p.error("name a LOCATION to comment on")
    if args.message is not None and args.file is not None:
        p.error("give the comment with one of -m or -F")
    message = read_message(args.message, args.file) if given else ""
    if not message:
        p.error("the comment is empty")
    return message


def _located(p: argparse.ArgumentParser, cwd: Path, raw: str, target: CommentTarget) -> CommentLocation:
    """*raw* parsed and checked against the target's commit. Exits 2 on a location that does not fit."""
    try:
        location = repository_path(cwd, parse_location(raw))
        if target.commit is None:
            return close_unchecked(location)
        return fit_to_file(location, file_lines(cwd, target.commit, location.path), commit=target.commit)
    except LocationError as error:
        p.error(str(error))


def _emit(payload: dict[str, object], text: str, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2))
        return
    print(text)
    if payload.get("url"):
        print(f"  {payload['url']}")


def _write(
    service: GerritService, target: CommentTarget, location: CommentLocation, message: str, *, unresolved: bool
) -> tuple[dict[str, object], str]:
    """Write the comment where it goes; return its JSON payload and its human line."""
    if target.patch_set is None:
        where = f"commit {target.commit[:7]}" if target.commit else target.key
        raise ChangeResolutionError(f"{where} is not a patchset on Gerrit: there is nowhere to put a draft")
    body = {**location.as_gerrit(), "message": message, "unresolved": unresolved}
    draft_id = service.comments.create_draft(target.key, target.patch_set, body)
    payload: dict[str, object] = {
        "action": "created",
        "state": "draft",
        "id": draft_id,
        "change": target.key,
        "commit": target.commit,
        "path": location.path,
        "line": location.line,
        "range": location.range.as_gerrit() if location.range else None,
        "patch_set": target.patch_set,
        "unresolved": unresolved,
        "url": gerrit_inline_comment_url(target.change_url, draft_id),
    }
    return payload, f"draft on {location.describe()} (patchset {target.patch_set})"


def _discard(service: GerritService, target: CommentTarget, draft_id: str, *, as_json: bool) -> None:
    removed = service.comments.discard_draft(target.key, draft_id) if target.change_url else None
    if removed is None:
        raise ChangeResolutionError(f"no draft {draft_id} of yours on {target.key}")
    loc = removed.path if removed.line is None else f"{removed.path}:{removed.line}"
    payload: dict[str, object] = {
        "action": "discarded",
        "state": "draft",
        "id": draft_id,
        "change": target.key,
        "path": removed.path,
        "line": removed.line,
        "message": removed.message,
    }
    _emit(payload, f'discarded draft on {loc}: "{first_line(removed.message)}"', as_json=as_json)


def main(argv: list[str] | None = None, *, gerrit: GerritRest | None = None) -> int:
    """Start a comment chain as a draft on a change, or discard one of your drafts."""
    return run_cli_command(lambda: _run(argv, gerrit=gerrit))


def _run(argv: list[str] | None, *, gerrit: GerritRest | None) -> int:
    p = _build_parser()
    args = p.parse_args(argv)
    message = _check_args(p, args)
    cwd, settings, _ = init_cli_runtime(args)
    branch = args.branch or resolve_working_branch(cwd, settings=settings)
    service = GerritService.from_cwd(cwd, settings=settings, rest=gerrit)
    review = load_state(cwd) if args.change is None else None
    if review is not None:
        # Staged, HEAD sits at the parent: the review state names the commit under review.
        change = review.change
        target = target_for_commit(service, change.key, change.change_id, change.commit, summary=change.subject)
    else:
        target = resolve_comment_target(service, cwd, args.change, branch=branch)
    if args.discard is not None:
        _discard(service, target, args.discard.strip(), as_json=args.json_)
        return int(ExitCode.OK)
    location = _located(p, cwd, args.location, target)
    payload, text = _write(service, target, location, message, unresolved=not args.resolved)
    _emit(payload, text, as_json=args.json_)
    return int(ExitCode.OK)


if __name__ == "__main__":
    raise SystemExit(main())
