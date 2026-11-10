"""CLI for ``ger review``: check a change out to review it, staged; walk its chain; vote."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from gerrit_workflow_tools.cli_common import (
    ExitCode,
    add_color_args,
    add_json_arg,
    add_verbose_and_debug_log_args,
    ask_choice,
    confirm,
    init_cli_runtime,
    run_cli_command,
)
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.config_write import ConfigWrite, apply_config_write
from gerrit_workflow_tools.core.gerrit.change_view import ChangeView
from gerrit_workflow_tools.core.gerrit.comments import PUBLISH_GUARD_ENV, publish_blocked
from gerrit_workflow_tools.core.gerrit.rest import GerritRest
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.review_checkout import (
    CheckoutOptions,
    ReviewChainEndError,
    ReviewMode,
    ReviewRefusal,
    ReviewState,
    ReviewUsageError,
    close_review,
    find_review,
    neighbour,
    open_review,
    set_staged,
)
from gerrit_workflow_tools.core.review_target import patchset_to_review, related_chain_numbers
from gerrit_workflow_tools.core.upstream_interactive import stdin_is_terminal
from gerrit_workflow_tools.render.comments import drafts_in, format_pending_drafts
from gerrit_workflow_tools.render.review_json import review_state_json


_VOTE_RE = re.compile(r"^([A-Za-z][\w-]*)\s*(?:=\s*([+-]?\d+)|([+-]\d+))$")
_LABEL_ALIASES = {"cr": "Code-Review", "v": "Verified"}


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the command-line parser for ``ger review``."""
    p = argparse.ArgumentParser(
        prog="ger review",
        usage=(
            "ger review CHANGEISH [--patchset N] [--stage | --no-stage] [--in-worktree | --in-place] [--force]\n"
            "       ger review (--next | --prev) [--stage | --no-stage] [--force]\n"
            "       ger review (--stage | --unstage | --status | --done)\n"
            "       ger review --vote LABEL=N ... [-m MSG] [--dry-run] [-y]"
        ),
        description=(
            "Check a change out to review it, with its diff staged so every git tool shows what it changed. "
            "Walk its review chain and publish your drafts with a vote."
        ),
    )
    p.add_argument("changeish", nargs="?", metavar="CHANGEISH", help="The change to review (number, URL, triplet).")
    p.add_argument("--patchset", type=int, default=None, metavar="N", help="Review patchset N (default: current).")
    walk = p.add_mutually_exclusive_group()
    walk.add_argument("--next", action="store_true", help="Review the change above in its review chain.")
    walk.add_argument("--prev", action="store_true", help="Review the change below in its review chain.")
    walk.add_argument("--status", action="store_true", help="Show what is being reviewed.")
    walk.add_argument("--done", action="store_true", help="Leave the review and delete its change-<n> branches.")
    stage = p.add_mutually_exclusive_group()
    stage.add_argument("--stage", dest="stage", action="store_true", default=None, help="Stage the change's diff.")
    stage.add_argument("--no-stage", "--unstage", dest="stage", action="store_false", help="Do not stage it.")
    where = p.add_mutually_exclusive_group()
    where.add_argument("--in-worktree", dest="mode", action="store_const", const="worktree", help="Review worktree.")
    where.add_argument("--in-place", dest="mode", action="store_const", const="branch", help="This checkout.")
    p.add_argument("--force", action="store_true", help="Move a change-<n> branch that has commits of yours.")
    vote = p.add_argument_group("voting")
    vote.add_argument("--vote", action="append", default=None, metavar="LABEL=N", help="Vote, e.g. CR+1, V=-1.")
    vote.add_argument("--change", metavar="CHANGEISH", default=None, help="With --vote: vote on this change.")
    vote.add_argument("-m", "--message", action="append", default=None, help="With --vote: a change message.")
    vote.add_argument("--dry-run", action="store_true", help="With --vote: list what would be sent.")
    vote.add_argument("-y", "--yes", action="store_true", help="With --vote: skip the confirmation.")
    add_json_arg(p)
    add_color_args(p)
    add_verbose_and_debug_log_args(p, debug_log_help="Log git and Gerrit calls to stderr.")
    return p


def _parse_votes(p: argparse.ArgumentParser, raw: list[str]) -> dict[str, int]:
    votes: dict[str, int] = {}
    for item in raw:
        match = _VOTE_RE.match(item.strip())
        if match is None:
            p.error(f"not a vote: {item!r} (use LABEL=N, LABEL+N or LABEL-N)")
        label = _LABEL_ALIASES.get(match.group(1).lower(), match.group(1))
        votes[label] = int(match.group(2) or match.group(3))
    return votes


def _check_args(p: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    opens = args.changeish is not None or args.next or args.prev
    if args.vote is None and (args.change or args.message or args.dry_run or args.yes):
        p.error("--change, -m, --dry-run and -y belong to --vote")
    if args.vote is not None and (opens or args.status or args.done or args.stage is not None):
        p.error("--vote acts on the change being reviewed; give it on its own")
    if args.changeish is not None and (args.next or args.prev or args.status or args.done):
        p.error("name a CHANGEISH or walk with --next/--prev, not both")
    if not opens and (args.patchset is not None or args.mode is not None or args.force):
        p.error("--patchset, --in-worktree, --in-place and --force go with a CHANGEISH or --next/--prev")
    if not opens and args.vote is None and not args.status and not args.done and args.stage is None:
        p.error("name a CHANGEISH to review (or --next, --prev, --stage, --unstage, --status, --vote, --done)")


def _mode(args: argparse.Namespace, cwd: Path, settings: Settings) -> ReviewMode:
    """The checkout mode: the flag, ``ger.reviewCheckout``, or ask once and remember."""
    if args.mode is not None:
        return args.mode  # type: ignore[no-any-return]
    if settings.review_checkout is not None:
        return settings.review_checkout  # type: ignore[return-value]
    if args.json_ or not stdin_is_terminal():
        return "worktree"
    answer: ReviewMode = ask_choice(
        "Check out reviews in a separate worktree (w, default) or in this checkout (b)? ",
        {"": "worktree", "w": "worktree", "b": "branch"},
        on_eof="worktree",
    )
    apply_config_write(cwd, ConfigWrite("ger.reviewCheckout", "set", answer))
    print(f"saved: git config ger.reviewCheckout {answer}", file=sys.stderr)
    return answer


def _emit_state(state: ReviewState, *, chain: list[int] | None, url: str | None, as_json: bool) -> None:
    if as_json:
        print(json.dumps(review_state_json(state, chain=chain, url=url), indent=2))
        return
    staged = " (staged)" if state.staged else ""
    print(f'reviewing {state.number} patchset {state.patch_set} "{state.change.subject}"{staged}')
    print(f"  {'worktree' if state.mode == 'worktree' else 'checkout'}: {state.worktree}")
    if chain and len(chain) > 1:
        print("  chain: " + " <- ".join(f"[{n}]" if n == state.number else str(n) for n in chain))
    if url:
        print(f"  {url}")


def _open(args: argparse.Namespace, service: GerritService, cwd: Path, state: ReviewState | None) -> int:
    settings = service.settings
    if args.changeish is not None:
        target, url = patchset_to_review(service, cwd, args.changeish, args.patchset)
        mode = _mode(args, cwd, settings)
    else:
        if state is None:
            raise ReviewUsageError("no review going on here: name a CHANGEISH first")
        number = neighbour(
            related_chain_numbers(service, state.key, state.patch_set), state.number, 1 if args.next else -1
        )
        target, url = patchset_to_review(service, cwd, f"change:{number}", None)
        mode = state.mode
    stage = settings.review_stage if args.stage is None else args.stage
    options = CheckoutOptions(mode=mode, stage=stage, force=args.force)
    new, moved_from = open_review(cwd, target, settings=settings, options=options)
    if moved_from is not None:
        print(f"moved {new.branch} off your commits; its old tip was {moved_from}", file=sys.stderr)
    _emit_state(new, chain=related_chain_numbers(service, new.key, new.patch_set), url=url, as_json=args.json_)
    return int(ExitCode.OK)


def _vote_target(args: argparse.Namespace, service: GerritService, cwd: Path) -> tuple[str, int, int | None]:
    """``(triplet, number, reviewed patchset)`` to vote on: ``--change``, else the review going on."""
    if args.change is not None:
        target, _url = patchset_to_review(service, cwd, args.change, None)
        return target.key, target.number, None
    state = find_review(cwd, service.settings)
    if state is None:
        raise ReviewUsageError("no review going on here: name the change with --change")
    return state.key, state.number, state.patch_set


def _vote(args: argparse.Namespace, p: argparse.ArgumentParser, service: GerritService, cwd: Path) -> int:
    votes = _parse_votes(p, args.vote)
    if publish_blocked() and not args.dry_run:
        p.error(f"publishing is blocked: {PUBLISH_GUARD_ENV} is set in the environment")
    key, number, reviewed = _vote_target(args, service, cwd)
    current = ChangeView.from_payload(service.rest.get_change(key)).current_revision_number
    if reviewed is not None and current != reviewed:
        print(
            f"change {number} is at patchset {current}, you reviewed {reviewed}: run ger review {number} first",
            file=sys.stderr,
        )
        return int(ExitCode.ATTENTION)
    chains = service.comments.chains_with_drafts(key)
    count = len(drafts_in(chains))
    voted = ", ".join(f"{label}{value:+d}" for label, value in votes.items())
    if not args.json_:
        print("\n".join(format_pending_drafts(f"change {number}: {count} draft(s), {voted}", chains)))
    if args.dry_run:
        return int(ExitCode.OK)
    if not args.yes:
        if args.json_ or not stdin_is_terminal():
            print("not publishing: pass -y to publish without a prompt", file=sys.stderr)
            return int(ExitCode.ATTENTION)
        if not confirm(f"Publish {count} draft(s) and {voted} on {number}?", default=False):
            return int(ExitCode.ATTENTION)
    message = "\n\n".join(args.message) if args.message else None
    service.comments.publish_drafts(key, message=message, labels=votes)
    if args.json_:
        print(json.dumps({"change": key, "drafts": count, "labels": votes, "status": "published"}, indent=2))
    else:
        print(f"published {count} draft(s) and {voted} on {number}")
    return int(ExitCode.OK)


def main(argv: list[str] | None = None, *, gerrit: GerritRest | None = None) -> int:
    """Check out, stage, walk, vote on or leave a review."""
    return run_cli_command(lambda: _run(argv, gerrit=gerrit))


def _run(argv: list[str] | None, *, gerrit: GerritRest | None) -> int:
    p = _build_parser()
    args = p.parse_args(argv)
    _check_args(p, args)
    cwd, settings, _ = init_cli_runtime(args)
    try:
        return _act(args, p, cwd, settings, gerrit)
    except ReviewRefusal as refusal:
        print(f"error: {refusal}", file=sys.stderr)
        return int(ExitCode.ATTENTION)
    except ReviewUsageError as error:
        print(f"error: {error}", file=sys.stderr)
        return int(ExitCode.USAGE)
    except ReviewChainEndError as end:
        print(f"error: {end}", file=sys.stderr)
        return int(ExitCode.NO_EDIT_ATTENTION)


def _act(
    args: argparse.Namespace, p: argparse.ArgumentParser, cwd: Path, settings: Settings, gerrit: GerritRest | None
) -> int:
    state = find_review(cwd, settings)
    if args.status:
        if state is None:
            print(json.dumps({"review": None}) if args.json_ else "no review going on")
        else:
            _emit_state(state, chain=None, url=None, as_json=args.json_)
        return int(ExitCode.OK)
    if args.done:
        if state is None:
            raise ReviewUsageError("no review going on here")
        deleted, kept = close_review(state)
        if args.json_:
            print(json.dumps({"done": state.number, "deleted": deleted, "kept": kept}, indent=2))
        else:
            print(f"done reviewing {state.number}; deleted {', '.join(deleted) or 'no branches'}")
            for branch in kept:
                print(f"  kept {branch}: it has commits of yours")
        return int(ExitCode.OK)
    if args.vote is None and args.changeish is None and not args.next and not args.prev:
        if state is None:
            raise ReviewUsageError("no review going on here: name a CHANGEISH first")
        _emit_state(set_staged(state, bool(args.stage)), chain=None, url=None, as_json=args.json_)
        return int(ExitCode.OK)
    service = GerritService.from_cwd(cwd, settings=settings, rest=gerrit)
    if args.vote is not None:
        return _vote(args, p, service, cwd)
    return _open(args, service, cwd, state)


if __name__ == "__main__":
    raise SystemExit(main())
