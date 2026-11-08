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
    Anchor,
    CommentLocation,
    LocationError,
    anchor_at,
    close_unchecked,
    file_lines,
    fit_to_file,
    parse_location,
    repository_path,
)
from gerrit_workflow_tools.core.comment_target import (
    CommentTarget,
    local_target,
    resolve_comment_target,
    target_for_commit,
)
from gerrit_workflow_tools.core.config import ConfigError, Settings
from gerrit_workflow_tools.core.gerrit.rest import GerritRest
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.gerrit.stack_context import ChangeResolutionError
from gerrit_workflow_tools.core.gerrit_change_status import CommentState, LocalCommentState, gerrit_inline_comment_url
from gerrit_workflow_tools.core.git_state import resolve_working_branch
from gerrit_workflow_tools.core.local_comments import LocalComment, LocalCommentStore, new_comment
from gerrit_workflow_tools.core.review_checkout import load_state
from gerrit_workflow_tools.render.comments import first_line


_LABELS: dict[str, str] = {"note": "note", "local-draft": "local draft", "draft": "draft"}


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the command-line parser for ``ger comment``."""
    p = argparse.ArgumentParser(
        prog="ger comment",
        usage=(
            "ger comment [options] LOCATION (-m MSG | -F FILE) [--note | --local]\n"
            "       ger comment --discard ID [--change CHANGEISH] [--json]"
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
    local = p.add_mutually_exclusive_group()
    local.add_argument("--note", action="store_true", help="A note: local and private, never uploaded.")
    local.add_argument("--local", action="store_true", help="A local draft, even on a patchset Gerrit has.")
    p.add_argument("--discard", metavar="ID", default=None, help="Delete your comment with this id instead.")
    add_json_arg(p)
    add_color_args(p)
    add_verbose_and_debug_log_args(p, debug_log_help="Log Gerrit calls to stderr.")
    return p


def _check_args(p: argparse.ArgumentParser, args: argparse.Namespace) -> str:
    """The message (empty with ``--discard``). Exits 2 on conflicting or missing arguments."""
    given = args.message is not None or args.file is not None
    if args.discard is not None:
        if args.location or given or args.resolved or args.note or args.local:
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


def _state(args: argparse.Namespace, target: CommentTarget, service: GerritService | None) -> CommentState:
    """Where the comment goes: ``--note``/``--local``, else a draft on Gerrit's patchset, else local."""
    if args.note:
        return "note"
    if args.local or service is None or not target.on_gerrit:
        return "local-draft"
    return "draft"


def _payload(target: CommentTarget, location: CommentLocation, **fields: object) -> dict[str, object]:
    return {
        "action": "created",
        "change": target.key,
        "commit": target.commit,
        "path": location.path,
        "line": location.line,
        "range": location.range.as_gerrit() if location.range else None,
        **fields,
    }


def _emit(payload: dict[str, object], text: str, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2))
        return
    print(text)
    if payload.get("url"):
        print(f"  {payload['url']}")


def _write_draft(
    service: GerritService, target: CommentTarget, location: CommentLocation, message: str, *, unresolved: bool
) -> tuple[dict[str, object], str]:
    """Write a Gerrit draft on the target's patchset; return its JSON payload and its human line."""
    assert target.patch_set is not None
    body = {**location.as_gerrit(), "message": message, "unresolved": unresolved}
    draft_id = service.comments.create_draft(target.key, target.patch_set, body)
    url = gerrit_inline_comment_url(target.change_url, draft_id)
    fields = {"state": "draft", "id": draft_id, "patch_set": target.patch_set, "unresolved": unresolved, "url": url}
    return _payload(target, location, **fields), f"draft on {location.describe()} (patchset {target.patch_set})"


def _anchor(cwd: Path, target: CommentTarget, location: CommentLocation) -> Anchor:
    """The anchor of *location* in the target's commit, which a local comment needs to be here."""
    commit = target.commit
    lines = file_lines(cwd, commit, location.path) if commit else None
    if commit is None or lines is None:
        raise ChangeResolutionError(f"{target.key}: its commit is not here, and a local comment is anchored to it")
    return anchor_at(cwd, commit, location, lines)


def _store_local(cwd: Path, target: CommentTarget, comment: LocalComment) -> tuple[dict[str, object], str]:
    """Add a note or local draft to the local comment store; return its JSON payload and human line."""
    LocalCommentStore(cwd).add(comment)
    location, commit, state = comment.anchor.location, comment.anchor.commit[:7], comment.state
    fields = {"state": state, "id": comment.id, "patch_set": None, "unresolved": comment.unresolved, "url": None}
    where = f"commit {commit}, not on Gerrit" if state == "local-draft" else f"commit {commit}"
    return _payload(target, location, **fields), f"{_LABELS[state]} on {location.describe()} ({where}) id {comment.id}"


def _discard(
    cwd: Path, service: GerritService | None, target: CommentTarget, comment_id: str, *, as_json: bool
) -> None:
    """Delete your local comment, else your Gerrit draft, with *comment_id*; print what it said."""
    local = LocalCommentStore(cwd).remove(comment_id, key=target.key)
    state: CommentState
    if local is not None:
        where, message, state, change = local.anchor.location.describe(), local.message, local.state, local.change
    else:
        removed = service.comments.discard_draft(target.key, comment_id) if service and target.change_url else None
        if removed is None:
            raise ChangeResolutionError(f"no comment {comment_id} of yours on {target.key}")
        where = removed.path if removed.line is None else f"{removed.path}:{removed.line}"
        message, state, change = removed.message, "draft", target.key
    payload: dict[str, object] = {
        "action": "discarded",
        "state": state,
        "id": comment_id,
        "change": change,
        "message": message,
    }
    _emit(payload, f'discarded {_LABELS[state]} on {where}: "{first_line(message)}"', as_json=as_json)


def _service(cwd: Path, settings: Settings, gerrit: GerritRest | None, *, local_only: bool) -> GerritService | None:
    """The Gerrit service; ``None`` when Gerrit is not configured and the comment stays local anyway."""
    try:
        return GerritService.from_cwd(cwd, settings=settings, rest=gerrit)
    except ConfigError:
        if local_only:
            return None
        raise


def _target(args: argparse.Namespace, cwd: Path, settings: Settings, service: GerritService | None) -> CommentTarget:
    """The change under review in this worktree, else ``--change`` or ``HEAD``."""
    branch = args.branch or resolve_working_branch(cwd, settings=settings)
    review = load_state(cwd) if args.change is None else None
    if service is None:
        return local_target(cwd, args.change, settings=settings, branch=branch)
    if review is not None:
        # Staged, HEAD sits at the parent: the review state names the commit under review.
        change = review.change
        return target_for_commit(service, change.key, change.change_id, change.commit)
    return resolve_comment_target(service, cwd, args.change, branch=branch)


def main(argv: list[str] | None = None, *, gerrit: GerritRest | None = None) -> int:
    """Start a comment chain (a draft, local draft or note), or discard one of your comments."""
    return run_cli_command(lambda: _run(argv, gerrit=gerrit))


def _run(argv: list[str] | None, *, gerrit: GerritRest | None) -> int:
    p = _build_parser()
    args = p.parse_args(argv)
    message = _check_args(p, args)
    cwd, settings, _ = init_cli_runtime(args)
    service = _service(cwd, settings, gerrit, local_only=args.note or args.local or args.discard is not None)
    target = _target(args, cwd, settings, service)
    if args.discard is not None:
        _discard(cwd, service, target, args.discard.strip(), as_json=args.json_)
        return int(ExitCode.OK)
    location = _located(p, cwd, args.location, target)
    state = _state(args, target, service)
    if state == "draft" and service is not None:
        payload, text = _write_draft(service, target, location, message, unresolved=not args.resolved)
    else:
        local_state: LocalCommentState = "note" if state == "note" else "local-draft"
        comment = new_comment(
            state=local_state,
            change=target.key,
            anchor=_anchor(cwd, target, location),
            message=message,
            unresolved=not args.resolved,
        )
        payload, text = _store_local(cwd, target, comment)
    _emit(payload, text, as_json=args.json_)
    return int(ExitCode.OK)


if __name__ == "__main__":
    raise SystemExit(main())
