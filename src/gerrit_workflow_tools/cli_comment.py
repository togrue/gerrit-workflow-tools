"""CLI for ``ger comment``: start a comment chain on a line, a range or a file of a change, edit or discard yours."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from gerrit_workflow_tools.cli_common import (
    ExitCode,
    add_change_option,
    add_color_args,
    add_output_format_args,
    add_verbose_and_debug_log_args,
    init_cli_runtime,
    read_message,
    run_cli_command,
)
from gerrit_workflow_tools.core.changeish import change_label
from gerrit_workflow_tools.core.comment_anchor import CommentLocation, LocationError
from gerrit_workflow_tools.core.comment_chains import ChainRefError, parse_chain_ref
from gerrit_workflow_tools.core.comment_target import (
    CommentTarget,
    local_target,
    resolve_comment_target,
    reviewed_elsewhere_target,
    target_for_commit,
)
from gerrit_workflow_tools.core.comment_write import (
    WrittenComment,
    default_state,
    discard_comment,
    edit_comment,
    locate,
    write_comment,
)
from gerrit_workflow_tools.core.gerrit.rest import GerritRest
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.gerrit_change_status import CommentState
from gerrit_workflow_tools.core.review_checkout import ReviewState, find_review, load_state
from gerrit_workflow_tools.core.run_context import RunContext
from gerrit_workflow_tools.render.comments import first_line
from gerrit_workflow_tools.render.show_json import changed_comment_json, written_comment_json
from gerrit_workflow_tools.render.style import shown_path


_LABELS: dict[str, str] = {"note": "note", "local-draft": "local draft", "draft": "draft"}


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the command-line parser for ``ger comment``."""
    p = argparse.ArgumentParser(
        prog="ger comment",
        usage=(
            "ger comment [options] LOCATION (-m MSG | -F FILE) [--note | --local]\n"
            "       ger comment --edit ID (-m MSG | -F FILE) [--change CHANGEISH] [--resolved] [--json | --ai]\n"
            "       ger comment --discard ID [--change CHANGEISH] [--json | --ai]"
        ),
        description=(
            "Start a comment chain on a file, line or range. On a patchset Gerrit has, it is a draft "
            "there, visible only to you until published; otherwise a local draft in refs/ger/comments."
        ),
    )
    p.add_argument(
        "location",
        nargs="?",
        metavar="LOCATION",
        help="PATH, PATH:LINE, PATH:START-END or PATH:LINE.COL-LINE.COL (lines and columns from 1).",
    )
    add_change_option(p, "Comment on this change (default: the change under review here, else the commit at HEAD).")
    p.add_argument("--branch", metavar="NAME", default=None, help="Local branch whose stack to resolve in.")
    text = p.add_argument_group("message")
    text.add_argument("-m", "--message", action="append", default=None, help="Message; repeat for paragraphs.")
    text.add_argument("-F", "--file", metavar="FILE", default=None, help="Read the message from FILE ('-': stdin).")
    p.add_argument("--resolved", action="store_true", help="Start the chain resolved (an FYI).")
    local = p.add_mutually_exclusive_group()
    local.add_argument("--note", action="store_true", help="A note: local and private, never uploaded.")
    local.add_argument("--local", action="store_true", help="A local draft, even on a patchset Gerrit has.")
    p.add_argument(
        "--edit",
        metavar="ID",
        default=None,
        help="Give your unpublished comment with this id (or its Gerrit URL) a new message instead.",
    )
    p.add_argument(
        "--discard", metavar="ID", default=None, help="Delete your comment with this id (or its Gerrit URL) instead."
    )
    add_output_format_args(p)
    add_color_args(p)
    add_verbose_and_debug_log_args(p, debug_log_help="Log Gerrit calls to stderr.")
    return p


def _check_args(p: argparse.ArgumentParser, args: argparse.Namespace) -> str:
    """The message (empty with ``--discard``). Exits 2 on conflicting or missing arguments."""
    given = args.message is not None or args.file is not None
    if args.discard is not None:
        if args.location or given or args.resolved or args.note or args.local or args.edit is not None:
            p.error("--discard takes only an ID (and --change)")
        return ""
    if args.edit is not None:
        if args.location or args.note or args.local:
            p.error("--edit takes an ID and the new message; the comment stays where and what it is")
    elif not args.location:
        p.error("name a LOCATION to comment on")
    if args.message is not None and args.file is not None:
        p.error("give the comment with one of -m or -F")
    message = read_message(args.message, args.file) if given else ""
    if not message:
        p.error("the comment is empty")
    return message


def _comment_id(p: argparse.ArgumentParser, args: argparse.Namespace, raw: str) -> str:
    """The id ``--edit`` / ``--discard`` names; a comment URL names its change too (``args.change``)."""
    try:
        ref = parse_chain_ref(raw)
    except ChainRefError as error:
        p.error(str(error))
    if ref.url is not None:
        if args.change is not None:
            p.error("a comment URL already names its change; drop --change")
        args.change = ref.url
    return ref.comment_id.strip()


def _located(p: argparse.ArgumentParser, cwd: Path, raw: str, target: CommentTarget) -> CommentLocation:
    """*raw* parsed and checked against the target's commit. Exits 2 on a location that does not fit."""
    try:
        return locate(cwd, raw, target)
    except LocationError as error:
        p.error(str(error))


def _state(args: argparse.Namespace, target: CommentTarget, service: GerritService | None) -> CommentState:
    """Where the comment goes: ``--note``/``--local``, else a draft on Gerrit's patchset, else local."""
    if args.note:
        return "note"
    if args.local:
        return "local-draft"
    return default_state(target, gerrit=service is not None)


def _line(written: WrittenComment) -> str:
    """The human line for a comment just written."""
    where = written.location.describe()
    if written.state == "draft":
        return f"draft on {where} (patchset {written.patch_set})"
    commit = (written.commit or "")[:7]
    place = f"commit {commit}, not on Gerrit" if written.state == "local-draft" else f"commit {commit}"
    return f"{_LABELS[written.state]} on {where} ({place}) id {written.id}"


def _emit(payload: dict[str, object], text: str, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2))
        return
    print(text)
    if payload.get("url"):
        print(f"  {payload['url']}")


def _discard(
    cwd: Path, service: GerritService | None, target: CommentTarget, comment_id: str, *, as_json: bool
) -> None:
    """Delete your local comment, else your Gerrit draft, with *comment_id*; print what it said."""
    gone = discard_comment(cwd, service, target, comment_id)
    text = f'discarded {_LABELS[gone.state]} on {gone.where}: "{first_line(gone.message)}"'
    _emit(changed_comment_json("discarded", gone), text, as_json=as_json)


def _edit(
    cwd: Path,
    service: GerritService | None,
    target: CommentTarget,
    comment_id: str,
    message: str,
    *,
    resolved: bool,
    as_json: bool,
) -> None:
    """Give your unpublished comment *comment_id* the text *message*; print what it says now."""
    was = edit_comment(cwd, service, target, comment_id, message, unresolved=False if resolved else None)
    text = f'updated {_LABELS[was.state]} on {was.where}: "{first_line(message)}"'
    _emit(changed_comment_json("updated", was), text, as_json=as_json)


def _service(run: RunContext, *, local_only: bool) -> GerritService | None:
    """The run's Gerrit service; ``None`` when Gerrit is not configured and the comment stays local anyway."""
    return None if local_only and not run.gerrit.configured else run.gerrit


def _target(
    args: argparse.Namespace, run: RunContext, service: GerritService | None
) -> tuple[CommentTarget, ReviewState | None]:
    """The change under review in this worktree, else ``--change`` or ``HEAD``; or the change under
    review in another worktree, for a file only it has (that review is returned too)."""
    cwd, settings = run.cwd, run.settings
    branch = args.branch or run.repo.working_branch
    review = load_state(cwd) if args.change is None else None
    if service is None:
        return local_target(cwd, args.change, settings=settings, branch=branch), None
    if review is not None:
        # Staged, HEAD sits at the parent: the review state names the commit under review.
        change = review.change
        return target_for_commit(service, change.key, change.change_id, change.commit), None
    away = find_review(cwd, settings) if args.change is None and args.location else None
    if away is not None and (target := reviewed_elsewhere_target(service, cwd, args.location, away)):
        return target, away
    return resolve_comment_target(service, cwd, args.change, branch=branch), None


def main(argv: list[str] | None = None, *, gerrit: GerritRest | None = None) -> int:
    """Start a comment chain (a draft, local draft or note), or edit or discard one of your comments."""
    return run_cli_command(lambda: _run(argv, gerrit=gerrit))


def _run(argv: list[str] | None, *, gerrit: GerritRest | None) -> int:
    p = _build_parser()
    args = p.parse_args(argv)
    message = _check_args(p, args)
    by_id = args.discard if args.discard is not None else args.edit
    comment_id = _comment_id(p, args, by_id) if by_id is not None else ""
    run = init_cli_runtime(args, gerrit=gerrit)
    cwd = run.cwd
    service = _service(run, local_only=args.note or args.local or by_id is not None)
    target, away = _target(args, run, service)
    if args.discard is not None:
        _discard(cwd, service, target, comment_id, as_json=args.json_)
        return int(ExitCode.OK)
    if args.edit is not None:
        _edit(cwd, service, target, comment_id, message, resolved=args.resolved, as_json=args.json_)
        return int(ExitCode.OK)
    location = _located(p, cwd, args.location, target)
    written = write_comment(
        cwd, service, target, location, message, state=_state(args, target, service), unresolved=not args.resolved
    )
    payload = written_comment_json(written)
    if away is not None:
        payload["under_review_in"] = away.worktree
        if not args.json_:
            print(f"comment on {change_label(away.number)} (under review in {shown_path(away.worktree, cwd)})")
    _emit(payload, _line(written), as_json=args.json_)
    return int(ExitCode.OK)


if __name__ == "__main__":
    raise SystemExit(main())
