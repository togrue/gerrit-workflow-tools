"""CLI for ``ger reply``: answer a comment chain with a draft, discard it, or publish your drafts."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass

from gerrit_workflow_tools.cli_args import (
    add_branch_arg,
    add_change_option,
    add_color_args,
    add_dry_run_arg,
    add_message_args,
    add_output_format_args,
    add_verbose_and_debug_log_args,
    add_vote_arg,
    add_yes_arg,
    read_message,
)
from gerrit_workflow_tools.cli_common import ExitCode, confirm_publish, init_cli_runtime, run_cli_command
from gerrit_workflow_tools.core.annotated_stack import annotate
from gerrit_workflow_tools.core.changeish import as_change_only
from gerrit_workflow_tools.core.comment_chains import (
    ChainRef,
    ChainRefError,
    find_chains,
    gerrit_inline_comment_url,
    parse_chain_ref,
)
from gerrit_workflow_tools.core.gerrit.change_resolution import ChangeAmbiguousError
from gerrit_workflow_tools.core.gerrit.comments import PUBLISH_GUARD_ENV, ChainMatch, DraftReply, publish_blocked
from gerrit_workflow_tools.core.gerrit.rest import GerritApiError, GerritRest
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.gerrit.stack_context import ChangeResolutionError
from gerrit_workflow_tools.core.gerrit_change_status import InlineComment
from gerrit_workflow_tools.core.gerrit_show import gerrit_rest_key, resolve_show_targets
from gerrit_workflow_tools.core.run_context import RunContext
from gerrit_workflow_tools.core.usage import UsageError
from gerrit_workflow_tools.core.votes import parse_votes
from gerrit_workflow_tools.core.words import counted
from gerrit_workflow_tools.render.comments import (
    chain_location,
    draft_transition,
    drafts_elsewhere,
    drafts_in,
    first_line,
    format_pending_drafts,
)
from gerrit_workflow_tools.upstream_interactive import require_branch_upstream


_DONE = "Done"


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the command-line parser for ``ger reply``."""
    p = argparse.ArgumentParser(
        prog="ger reply",
        usage=(
            "ger reply [options] CHAIN (-m MSG | -F FILE | --done | --discard)\n"
            "       ger reply --publish [REV ...] [--only ID ... | --keep-drafts] [--vote LABEL=N ...] [-m MSG] "
            "[--dry-run] [-y]"
        ),
        description=(
            "Answer a comment chain with a draft. Drafts are visible only to you until published "
            "(ger push publishes them with the patchset). --publish sends your review: every draft, or "
            "only --only ones, with --vote votes and a -m change message. On the changes you name, -m "
            "or --vote alone posts when there is no draft to publish; --keep-drafts posts them without "
            "publishing any: a change-level note."
        ),
    )
    p.add_argument(
        "targets",
        nargs="*",
        metavar="CHAIN",
        help=(
            "The chain to answer: PATH:LINE (the one open chain there), any comment id in it or a unique "
            "prefix of one (ger show --json), or the comment's Gerrit URL. "
            "With --publish: the changes whose drafts to publish (default: the local stack): a change "
            "number, commit, Change-Id, change:<n>, triplet or URL."
        ),
    )
    add_change_option(p, "Look for CHAIN on this change only (default: every change in the local stack).")
    add_branch_arg(p)
    text = add_message_args(p)
    text.add_argument("--done", action="store_true", help=f"Reply {_DONE!r} and resolve the chain.")
    state = p.add_mutually_exclusive_group()
    state.add_argument("--resolve", action="store_true", help="Resolve the chain once the draft is published.")
    state.add_argument("--unresolve", action="store_true", help="Keep or reopen the chain once published.")
    p.add_argument("--discard", action="store_true", help="Delete your draft in the chain instead of writing one.")
    publish = p.add_argument_group("publishing")
    publish.add_argument(
        "--publish", action="store_true", help="Publish your drafts on the target changes (all, unless --only)."
    )
    publish.add_argument(
        "--only",
        action="append",
        default=None,
        metavar="ID",
        help="With --publish: publish only your draft in this chain (the CHAIN id you replied to, or the "
        "draft's own id); repeat for more. Your other drafts stay drafts.",
    )
    publish.add_argument(
        "--keep-drafts",
        action="store_true",
        help="With --publish: publish no drafts, only the -m message and --vote votes (a note on a named change).",
    )
    add_vote_arg(publish, scope="--publish")
    add_dry_run_arg(publish, scope="--publish")
    add_yes_arg(publish, scope="--publish")
    add_output_format_args(p)
    add_color_args(p)
    add_verbose_and_debug_log_args(p)
    return p


def _message_and_flag(p: argparse.ArgumentParser, args: argparse.Namespace) -> tuple[str, bool | None]:
    """The reply text and the requested state (``None``: keep or inherit). Exits 2 on conflicts."""
    sources = [name for name, given in (("-m", args.message), ("-F", args.file), ("--done", args.done)) if given]
    if args.dry_run or args.yes or args.only or args.vote or args.keep_drafts:
        p.error("--only, --keep-drafts, --vote, --dry-run and -y belong to --publish")
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


def _chain_ref(p: argparse.ArgumentParser, args: argparse.Namespace) -> tuple[ChainRef, str | None]:
    """The chain CHAIN names, and the changeish to look on (``--change``, or a comment URL's change)."""
    if len(args.targets) != 1:
        p.error("name exactly one CHAIN to answer (or use --publish)")
    try:
        ref = parse_chain_ref(args.targets[0])
    except ChainRefError as error:
        p.error(str(error))
    if ref.url is not None and args.change is not None:
        p.error("a comment URL already names its change; drop --change")
    return ref, ref.url or args.change


def _check_publish_args(p: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Exit 2 on reply-only flags with ``--publish``, and while the publish guard is set."""
    if args.file or args.done or args.discard or args.resolve or args.unresolve or args.change:
        p.error("--publish takes only -m (the change message), --only, --vote, --dry-run, -y and REVs")
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


def _gerrit_changes(run: RunContext, revs: list[str], *, branch: str | None) -> tuple[list[_Change], str]:
    """The Gerrit changes *revs* name, else the local stack, else (no branch, e.g. detached) HEAD.

    Also returns a phrase naming where they came from, for messages.
    """
    service, cwd = run.gerrit, run.cwd
    stack = not revs and branch is not None
    targets = resolve_show_targets(
        cwd,
        revs,
        service.rest,
        settings=service.settings,
        stack=stack,
        branch=branch,
        changes=service.changes,
        head=run.repo.head,
    )
    commits = annotate([t.row for t in targets], service=service, cwd=cwd).require_gerrit()
    found: dict[str, _Change] = {}
    for target, commit in zip(targets, commits, strict=True):
        key = gerrit_rest_key(commit, target.resolution, service)
        if commit.pushed and key:
            found[key] = _Change(key, commit.freshness, commit.gerrit_url, commit.summary)
    return list(found.values()), (", ".join(revs) if revs else "the local stack" if stack else "HEAD")


def _chain_line(match: ChainMatch) -> str:
    """One chain for a list in an error: its id, where it is, its first words."""
    words = first_line(match.chain.comments[0].message) if match.chain.comments else ""
    words = words if len(words) <= 40 else f"{words[:39]}…"
    return f"  {match.chain.root_id}  {chain_location(match.chain)}  {words}"


def _find(run: RunContext, ref: ChainRef, *, change: str | None, branch: str | None) -> tuple[ChainMatch, str | None]:
    """The one chain *ref* names, and the web URL of its change. Not one: say which there are."""
    changes, where = _gerrit_changes(run, [change] if change else [], branch=branch)
    chains = run.gerrit.comments.chains_on({c.key: c.freshness for c in changes})
    matches = find_chains(chains, ref, lambda match: match.chain)
    if len(matches) > 1:
        listed = "\n".join(_chain_line(m) for m in matches)
        raise ChangeAmbiguousError(
            f"{ref.raw} names {len(matches)} comment chains; give one id:\n{listed}", alternatives=[]
        )
    if not matches:
        open_chains = [m for m in chains if not m.chain.resolved]
        listed = "\n".join(_chain_line(m) for m in open_chains)
        hint = f"; open chains (id, place, first words):\n{listed}" if open_chains else "; it has no open chains"
        raise ChangeResolutionError(f"no comment {ref.raw} on {where}{hint}")
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


def _publish(args: argparse.Namespace, run: RunContext, branch: str | None) -> int:
    """List what goes out on the targets, confirm, and post it change by change.

    Your drafts (all, or ``--only`` ones), ``--vote`` votes and the ``-m`` message. A change
    without drafts to publish gets the message and votes only when it was named: a note
    nobody asked for never goes to every change of the stack.
    """
    votes = parse_votes(args.vote)
    if args.keep_drafts and args.only:
        raise UsageError("--keep-drafts publishes no drafts; it cannot be combined with --only")
    # Publishing acts on Gerrit changes, so a bare number names one (as for --change).
    service = run.gerrit
    changes, where = _gerrit_changes(run, [as_change_only(t) for t in args.targets], branch=branch)
    freshness = {c.key: c.freshness for c in changes}
    with_drafts = {} if args.keep_drafts else service.comments.drafts_to_publish(freshness, args.only, where=where)
    message = "\n\n".join(args.message) if args.message else None
    named = bool(args.targets) and bool(message or votes)
    posts = [c for c in changes if c.key in with_drafts or named]
    results: list[dict[str, object]] = [
        {"change": c.key, "url": c.url, "drafts": 0, "status": "no-drafts"} for c in changes if c not in posts
    ]
    extras = ", ".join([*(f"{label}{value:+d}" for label, value in votes.items()), *(["a message"] if message else [])])
    if not args.json_:
        for change in posts:
            heading = f"{change.summary}  {change.url or change.key}"
            print("\n".join(format_pending_drafts(heading, with_drafts.get(change.key, []))))
            if extras:
                print(f"  + {extras}")
        if not posts:
            print(f"no drafts to publish on {where}")
    # Nothing here, and nothing named: say where your drafts are instead (one has:draft query).
    elsewhere = _drafts_elsewhere(service, changes) if not posts and not args.targets else []
    if elsewhere and not args.json_:
        print(f"  {drafts_elsewhere(dict.fromkeys(elsewhere))}")
    count = sum(len(drafts_in(chains)) for chains in with_drafts.values())
    what = f"{counted(count, 'draft')}{f' and {extras}' if extras else ''} on {counted(len(posts), 'change')}"
    if posts and not args.dry_run and not confirm_publish(args, what):
        return int(ExitCode.OK)

    failed = False
    for change in posts:
        chains = with_drafts.get(change.key, [])
        drafts = [d.comment_id for _, d in drafts_in(chains) if d.comment_id]
        row: dict[str, object] = {"change": change.key, "url": change.url, "drafts": len(drafts), "labels": votes}
        if args.dry_run:
            row["status"] = "dry-run"
        else:
            try:
                service.comments.publish_drafts(
                    change.key,
                    message=message,
                    labels=votes or None,
                    draft_ids=None
                    if args.only is None and not args.keep_drafts and change.key in with_drafts
                    else drafts,
                )
                row["status"] = "published"
                if not args.json_:
                    with_extras = f" and {extras}" if extras else ""
                    print(f"published {counted(len(drafts), 'draft')}{with_extras} on {change.summary}")
            except GerritApiError as error:
                failed = True
                row.update(status="failed", error=str(error))
                print(f"gerrit error: {change.key}: {error}", file=sys.stderr)
        results.append(row)
    if args.json_:
        print(json.dumps({"changes": results, **({"drafts_elsewhere": elsewhere} if elsewhere else {})}, indent=2))
    return int(ExitCode.GERRIT) if failed else int(ExitCode.OK)


def _drafts_elsewhere(service: GerritService, changes: list[_Change]) -> list[int]:
    """Your changes beyond *changes* that hold drafts of yours, by number."""
    here = {change.key for change in changes}
    return [view.number for view in service.comments.drafted_changes() if view.number and view.key not in here]


def main(argv: list[str] | None = None, *, gerrit: GerritRest | None = None) -> int:
    """Write, update or discard your draft reply to one comment chain, or publish your drafts."""
    return run_cli_command(lambda: _run(argv, gerrit=gerrit))


def _run(argv: list[str] | None, *, gerrit: GerritRest | None) -> int:
    p = _build_parser()
    args = p.parse_args(argv)
    message, unresolved, ref, change = "", None, ChainRef(""), None
    if args.publish:
        _check_publish_args(p, args)
    else:
        message, unresolved = _message_and_flag(p, args)
        ref, change = _chain_ref(p, args)
    run = init_cli_runtime(args, gerrit=gerrit)
    cwd, settings = run.cwd, run.settings
    branch = args.branch or run.repo.working_branch
    searches_stack = change is None and not (args.publish and args.targets)
    if searches_stack and branch is not None and not require_branch_upstream(cwd, branch, settings=settings):
        return int(ExitCode.REFUSED)

    service = run.gerrit
    if args.publish:
        return _publish(args, run, branch)
    match, change_url = _find(run, ref, change=change, branch=branch)
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
