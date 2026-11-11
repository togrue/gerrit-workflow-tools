"""CLI for ``ger reply``: answer a comment chain with a draft, discard it, or publish your drafts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

from gerrit_workflow_tools.cli_common import (
    ExitCode,
    add_color_args,
    add_json_arg,
    add_verbose_and_debug_log_args,
    confirm,
    init_cli_runtime,
    read_message,
    run_cli_command,
)
from gerrit_workflow_tools.core.annotated_stack import annotate
from gerrit_workflow_tools.core.gerrit.change_resolution import ChangeAmbiguousError
from gerrit_workflow_tools.core.gerrit.comments import PUBLISH_GUARD_ENV, ChainMatch, DraftReply, publish_blocked
from gerrit_workflow_tools.core.gerrit.rest import GerritApiError, GerritRest
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.gerrit.stack_context import ChangeResolutionError
from gerrit_workflow_tools.core.gerrit_change_status import CommentChain, InlineComment, gerrit_inline_comment_url
from gerrit_workflow_tools.core.gerrit_show import gerrit_rest_key, resolve_show_targets
from gerrit_workflow_tools.core.git_state import resolve_working_branch
from gerrit_workflow_tools.core.upstream_interactive import require_branch_upstream, stdin_is_terminal
from gerrit_workflow_tools.core.usage import UsageError
from gerrit_workflow_tools.render.comments import (
    chain_location,
    draft_transition,
    drafts_in,
    first_line,
    format_pending_drafts,
)


_COMMENT_URL_RE = re.compile(r"/comment/([^/?#]+)")
_DONE = "Done"


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the command-line parser for ``ger reply``."""
    p = argparse.ArgumentParser(
        prog="ger reply",
        usage=(
            "ger reply [options] CHAIN (-m MSG | -F FILE | --done | --discard)\n"
            "       ger reply --publish [-m MSG] [--dry-run] [-y] [REV ...]"
        ),
        description=(
            "Answer a comment chain with a draft. Drafts are visible only to you until published "
            "(ger push publishes them with the patchset)."
        ),
    )
    p.add_argument(
        "targets",
        nargs="*",
        metavar="CHAIN",
        help=(
            "Any comment id in the chain (from ger show --json), or the comment's Gerrit URL. "
            "With --publish: changeishes whose drafts to publish (default: the local stack)."
        ),
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
    publish = p.add_argument_group("publishing")
    publish.add_argument("--publish", action="store_true", help="Publish all your drafts on the target changes.")
    publish.add_argument("--dry-run", action="store_true", help="With --publish: list the drafts, publish nothing.")
    publish.add_argument("-y", "--yes", action="store_true", help="With --publish: skip the confirmation.")
    add_json_arg(p)
    add_color_args(p)
    add_verbose_and_debug_log_args(p, debug_log_help="Log Gerrit calls to stderr.")
    return p


def _message_and_flag(p: argparse.ArgumentParser, args: argparse.Namespace) -> tuple[str, bool | None]:
    """The reply text and the requested state (``None``: keep or inherit). Exits 2 on conflicts."""
    sources = [name for name, given in (("-m", args.message), ("-F", args.file), ("--done", args.done)) if given]
    if args.dry_run or args.yes:
        p.error("--dry-run and -y belong to --publish")
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
    message = read_message(args.message, args.file)
    if not message:
        p.error("the reply is empty")
    return message, False if args.resolve else True if args.unresolve else None


def _chain_ref(p: argparse.ArgumentParser, args: argparse.Namespace) -> tuple[str, str | None]:
    """``(comment id, changeish or None)`` from CHAIN and ``--change``."""
    if len(args.targets) != 1:
        p.error("name exactly one CHAIN to answer (or use --publish)")
    chain = args.targets[0]
    if "://" not in chain:
        return chain.strip(), args.change
    match = _COMMENT_URL_RE.search(chain)
    if match is None:
        p.error(f"not a comment URL (no /comment/<id>/): {chain}")
    if args.change is not None:
        p.error("a comment URL already names its change; drop --change")
    return unquote(match.group(1)), chain


def _check_publish_args(p: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Exit 2 on reply-only flags with ``--publish``, and while the publish guard is set."""
    if args.file or args.done or args.discard or args.resolve or args.unresolve or args.change:
        p.error("--publish takes only -m (the change message), --dry-run, -y and REVs")
    if publish_blocked() and not args.dry_run:
        p.error(f"publishing is blocked: {PUBLISH_GUARD_ENV} is set in the environment")


@dataclass(frozen=True)
class _Change:
    """A change on Gerrit that a target resolved to."""

    key: str
    """REST key: the triplet, never a bare Change-Id that could name several branches."""
    freshness: str | None
    url: str | None
    summary: str


def _gerrit_changes(
    service: GerritService, cwd: Path, revs: list[str], *, branch: str | None
) -> tuple[list[_Change], str]:
    """The Gerrit changes *revs* name, else the local stack, else (no branch, e.g. detached) HEAD.

    Also returns a phrase naming where they came from, for messages.
    """
    stack = not revs and branch is not None
    targets = resolve_show_targets(cwd, revs, service.rest, settings=service.settings, stack=stack, branch=branch)
    commits = annotate([t.row for t in targets], service=service, cwd=cwd).require_gerrit()
    found: dict[str, _Change] = {}
    for target, commit in zip(targets, commits, strict=True):
        key = gerrit_rest_key(commit, target.resolution, service)
        if commit.pushed and key:
            found[key] = _Change(key, commit.freshness, commit.gerrit_url, commit.summary)
    return list(found.values()), (", ".join(revs) if revs else "the local stack" if stack else "HEAD")


def _find(
    service: GerritService, cwd: Path, comment_id: str, *, change: str | None, branch: str | None
) -> tuple[ChainMatch, str | None]:
    """The one chain holding *comment_id*, and the web URL of its change."""
    changes, where = _gerrit_changes(service, cwd, [change] if change else [], branch=branch)
    matches = service.comments.find_chain({c.key: c.freshness for c in changes}, comment_id)
    if not matches:
        raise ChangeResolutionError(f"no comment {comment_id} on {where} ({len(changes)} change(s) on Gerrit searched)")
    if len(matches) > 1:
        names = ", ".join(m.change for m in matches)
        raise ChangeAmbiguousError(f"comment {comment_id} is on several changes: {names}", alternatives=[])
    return matches[0], next(c.url for c in changes if c.key == matches[0].change)


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
        replaced = first_line(reply.replaced_message or "")
        print(f'updated draft on {loc}{draft_transition(match.chain, reply.unresolved)} (replaced: "{replaced}")')
    else:
        print(f"draft reply on {loc} (patchset {reply.patch_set}){draft_transition(match.chain, reply.unresolved)}")
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
    print(f'discarded draft on {chain_location(match.chain)}: "{first_line(removed.message)}"')


def _pending_drafts(service: GerritService, changes: list[_Change]) -> list[tuple[_Change, list[CommentChain]]]:
    """Each change that has drafts of yours, with the chains holding them."""
    out: list[tuple[_Change, list[CommentChain]]] = []
    for change in changes:
        with_drafts = service.comments.chains_with_drafts(change.key, change_updated=change.freshness)
        if with_drafts:
            out.append((change, with_drafts))
    return out


def _confirmed(args: argparse.Namespace, count: int, changes: int) -> bool:
    """Whether to go ahead: ``-y``, or a yes at a prompt. JSON output and no terminal never prompt."""
    if args.yes:
        return True
    if args.json_ or not stdin_is_terminal():
        raise UsageError("no prompt to answer (non-interactive or --json): pass -y to publish")
    return confirm(f"Publish {count} draft(s) on {changes} change(s)?", default=False)


def _publish(args: argparse.Namespace, service: GerritService, cwd: Path, branch: str | None) -> int:
    """List your drafts on the targets, confirm, and publish them change by change."""
    changes, where = _gerrit_changes(service, cwd, list(args.targets), branch=branch)
    pending = _pending_drafts(service, changes)
    with_drafts = {change.key for change, _ in pending}
    results: list[dict[str, object]] = [
        {"change": c.key, "url": c.url, "drafts": 0, "status": "no-drafts"} for c in changes if c.key not in with_drafts
    ]
    if not args.json_:
        for change, chains in pending:
            print("\n".join(format_pending_drafts(f"{change.summary}  {change.url or change.key}", chains)))
        if not pending:
            print(f"no drafts to publish on {where}")
    count = sum(len(drafts_in(chains)) for _, chains in pending)
    if pending and not args.dry_run and not _confirmed(args, count, len(pending)):
        return int(ExitCode.ATTENTION)

    message = "\n\n".join(args.message) if args.message else None
    failed = False
    for change, chains in pending:
        row: dict[str, object] = {"change": change.key, "url": change.url, "drafts": len(drafts_in(chains))}
        if args.dry_run:
            row["status"] = "dry-run"
        else:
            try:
                service.comments.publish_drafts(change.key, message=message)
                row["status"] = "published"
                if not args.json_:
                    print(f"published {row['drafts']} draft(s) on {change.summary}")
            except GerritApiError as error:
                failed = True
                row.update(status="failed", error=str(error))
                print(f"gerrit error: {change.key}: {error}", file=sys.stderr)
        results.append(row)
    if args.json_:
        print(json.dumps({"changes": results}, indent=2))
    return int(ExitCode.GERRIT) if failed else int(ExitCode.OK)


def main(argv: list[str] | None = None, *, gerrit: GerritRest | None = None) -> int:
    """Write, update or discard your draft reply to one comment chain, or publish your drafts."""
    return run_cli_command(lambda: _run(argv, gerrit=gerrit))


def _run(argv: list[str] | None, *, gerrit: GerritRest | None) -> int:
    p = _build_parser()
    args = p.parse_args(argv)
    message, unresolved, comment_id, change = "", None, "", None
    if args.publish:
        _check_publish_args(p, args)
    else:
        message, unresolved = _message_and_flag(p, args)
        comment_id, change = _chain_ref(p, args)
    cwd, settings, _ = init_cli_runtime(args)
    branch = args.branch or resolve_working_branch(cwd, settings=settings)
    searches_stack = change is None and not (args.publish and args.targets)
    if searches_stack and branch is not None and not require_branch_upstream(cwd, branch, settings=settings):
        return int(ExitCode.ATTENTION)

    service = GerritService.from_cwd(cwd, settings=settings, rest=gerrit)
    if args.publish:
        return _publish(args, service, cwd, branch)
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
