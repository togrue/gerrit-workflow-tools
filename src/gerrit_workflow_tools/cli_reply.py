"""CLI for ``ger reply``: answer a comment chain with a draft, or discard your draft."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote

from gerrit_workflow_tools.cli_common import (
    ExitCode,
    add_color_args,
    add_json_arg,
    add_verbose_and_debug_log_args,
    init_cli_runtime,
    run_cli_command,
)
from gerrit_workflow_tools.core.annotated_stack import annotate
from gerrit_workflow_tools.core.gerrit.change_resolution import ChangeAmbiguousError
from gerrit_workflow_tools.core.gerrit.comments import ChainMatch, DraftReply
from gerrit_workflow_tools.core.gerrit.rest import GerritRest
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.gerrit.stack_context import ChangeResolutionError
from gerrit_workflow_tools.core.gerrit_change_status import InlineComment, gerrit_inline_comment_url
from gerrit_workflow_tools.core.gerrit_show import gerrit_rest_key, resolve_show_targets
from gerrit_workflow_tools.core.git_state import resolve_working_branch
from gerrit_workflow_tools.core.upstream_interactive import require_branch_upstream
from gerrit_workflow_tools.render.comments import chain_location


_COMMENT_URL_RE = re.compile(r"/comment/([^/?#]+)")
_DONE = "Done"


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the command-line parser for ``ger reply``."""
    p = argparse.ArgumentParser(
        prog="ger reply",
        description=(
            "Answer a comment chain with a draft. Drafts are visible only to you until published "
            "(ger push publishes them with the patchset)."
        ),
    )
    p.add_argument(
        "chain",
        metavar="CHAIN",
        help="Any comment id in the chain (from ger show --json), or the comment's Gerrit URL.",
    )
    p.add_argument(
        "--change",
        metavar="CHANGEISH",
        default=None,
        help="Look for CHAIN on this change only (default: every change in the local stack).",
    )
    p.add_argument(
        "--branch",
        metavar="NAME",
        default=None,
        help="Local branch whose stack to search (default: working branch).",
    )
    text = p.add_argument_group("message")
    text.add_argument("-m", "--message", action="append", default=None, help="Message; repeat for paragraphs.")
    text.add_argument("-F", "--file", metavar="FILE", default=None, help="Read the message from FILE ('-': stdin).")
    text.add_argument("--done", action="store_true", help=f"Reply {_DONE!r} and resolve the chain.")
    state = p.add_mutually_exclusive_group()
    state.add_argument("--resolve", action="store_true", help="Resolve the chain once the draft is published.")
    state.add_argument("--unresolve", action="store_true", help="Keep or reopen the chain once published.")
    p.add_argument("--discard", action="store_true", help="Delete your draft in the chain instead of writing one.")
    add_json_arg(p)
    add_color_args(p)
    add_verbose_and_debug_log_args(p, debug_log_help="Log Gerrit calls to stderr.")
    return p


def _message_and_flag(p: argparse.ArgumentParser, args: argparse.Namespace) -> tuple[str, bool | None]:
    """The reply text and the requested state (``None``: keep or inherit). Exits 2 on conflicts."""
    sources = [name for name, given in (("-m", args.message), ("-F", args.file), ("--done", args.done)) if given]
    if args.discard:
        if sources or args.resolve or args.unresolve:
            p.error("--discard takes no message and no --resolve/--unresolve")
        return "", None
    if len(sources) != 1:
        p.error("give the reply with exactly one of -m, -F or --done")
    if args.done:
        if args.unresolve:
            p.error("--done resolves the chain; it cannot be combined with --unresolve")
        return _DONE, False
    if args.file is not None:
        message = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
    else:
        message = "\n\n".join(args.message)
    message = message.strip()
    if not message:
        p.error("the reply is empty")
    return message, False if args.resolve else True if args.unresolve else None


def _chain_ref(p: argparse.ArgumentParser, args: argparse.Namespace) -> tuple[str, str | None]:
    """``(comment id, changeish or None)`` from CHAIN and ``--change``."""
    if "://" not in args.chain:
        return args.chain.strip(), args.change
    match = _COMMENT_URL_RE.search(args.chain)
    if match is None:
        p.error(f"not a comment URL (no /comment/<id>/): {args.chain}")
    if args.change is not None:
        p.error("a comment URL already names its change; drop --change")
    return unquote(match.group(1)), args.chain


def _find(
    service: GerritService, cwd: Path, comment_id: str, *, change: str | None, branch: str | None
) -> tuple[ChainMatch, str | None]:
    """The one chain holding *comment_id*, and the web URL of its change.

    Searches *change* when given, else the local stack, else (no branch, e.g. detached) HEAD.
    """
    stack = change is None and branch is not None
    targets = resolve_show_targets(
        cwd, [change] if change else [], service.rest, settings=service.settings, stack=stack, branch=branch
    )
    commits = annotate([t.row for t in targets], service=service, cwd=cwd)
    changes: dict[str, str | None] = {}
    web: dict[str, str | None] = {}
    for target, commit in zip(targets, commits, strict=True):
        key = gerrit_rest_key(commit, target.resolution)
        if commit.pushed and key:
            # A bare Change-Id can name several branches; write to the stack's triplet.
            key = service.cache_key(key)
            changes[key], web[key] = commit.freshness, commit.gerrit_url
    where = change or ("the local stack" if stack else "HEAD")
    matches = service.comments.find_chain(changes, comment_id)
    if not matches:
        raise ChangeResolutionError(f"no comment {comment_id} on {where} ({len(changes)} change(s) on Gerrit searched)")
    if len(matches) > 1:
        names = ", ".join(m.change for m in matches)
        raise ChangeAmbiguousError(f"comment {comment_id} is on several changes: {names}", alternatives=[])
    return matches[0], web[matches[0].change]


def _first_line(text: str) -> str:
    return text.strip().splitlines()[0] if text.strip() else ""


def _transition(match: ChainMatch, unresolved: bool) -> str:
    if match.chain.resolved == (not unresolved):
        return ""
    return " -> reopens" if unresolved else " -> resolves"


def _emit_reply(match: ChainMatch, reply: DraftReply, url: str | None, *, as_json: bool) -> None:
    if as_json:
        payload = {
            "action": reply.action,
            "change": match.change,
            "root_id": match.chain.root_id,
            "draft_id": reply.draft_id,
            "patch_set": reply.patch_set,
            "unresolved": reply.unresolved,
            "replaced_message": reply.replaced_message,
            "url": url,
        }
        print(json.dumps(payload, indent=2))
        return
    loc = chain_location(match.chain)
    if reply.action == "updated":
        replaced = _first_line(reply.replaced_message or "")
        print(f'updated draft on {loc}{_transition(match, reply.unresolved)} (replaced: "{replaced}")')
    else:
        print(f"draft reply on {loc} (patchset {reply.patch_set}){_transition(match, reply.unresolved)}")
    if url:
        print(f"  {url}")


def _emit_discard(match: ChainMatch, removed: InlineComment, url: str | None, *, as_json: bool) -> None:
    if as_json:
        payload = {
            "action": "discarded",
            "change": match.change,
            "root_id": match.chain.root_id,
            "draft_id": removed.comment_id,
            "patch_set": removed.patch_set,
            "message": removed.message,
            "url": url,
        }
        print(json.dumps(payload, indent=2))
        return
    print(f'discarded draft on {chain_location(match.chain)}: "{_first_line(removed.message)}"')


def main(argv: list[str] | None = None, *, gerrit: GerritRest | None = None) -> int:
    """Write, update or discard your draft reply to one comment chain."""
    return run_cli_command(lambda: _run(argv, gerrit=gerrit))


def _run(argv: list[str] | None, *, gerrit: GerritRest | None) -> int:
    p = _build_parser()
    args = p.parse_args(argv)
    message, unresolved = _message_and_flag(p, args)
    comment_id, change = _chain_ref(p, args)
    cwd, settings, _ = init_cli_runtime(args)
    branch = args.branch or resolve_working_branch(cwd, settings=settings)
    if change is None and branch is not None and not require_branch_upstream(cwd, branch, settings=settings):
        return int(ExitCode.ATTENTION)

    service = GerritService.from_cwd(cwd, settings=settings, rest=gerrit)
    match, change_url = _find(service, cwd, comment_id, change=change, branch=branch)
    url = gerrit_inline_comment_url(change_url, match.chain.root_id)
    if args.discard:
        removed = service.comments.discard_reply(match.change, match.chain)
        _emit_discard(match, removed, url, as_json=args.json_)
    else:
        reply = service.comments.save_reply(match.change, match.chain, message, unresolved=unresolved)
        _emit_reply(match, reply, url, as_json=args.json_)
    return int(ExitCode.OK)


if __name__ == "__main__":
    raise SystemExit(main())
