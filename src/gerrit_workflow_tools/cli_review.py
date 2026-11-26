"""CLI for ``ger review``: check a change out to review it, staged; walk its chain; vote."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from gerrit_workflow_tools.cli_args import (
    add_change_option,
    add_color_args,
    add_dry_run_arg,
    add_message_args,
    add_output_format_args,
    add_patchset_arg,
    add_verbose_and_debug_log_args,
    add_vote_arg,
    add_yes_arg,
    changeish_help,
)
from gerrit_workflow_tools.cli_common import ExitCode, init_cli_runtime, may_prompt, run_cli_command
from gerrit_workflow_tools.cli_terminal import ask_choice
from gerrit_workflow_tools.core.commit_diff import commit_diff
from gerrit_workflow_tools.core.config import ConfigError, Settings
from gerrit_workflow_tools.core.config_write import ConfigWrite, apply_config_write
from gerrit_workflow_tools.core.gerrit.rest import GerritApiError, GerritRest
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.gerrit_patchset import resolve_patchset
from gerrit_workflow_tools.core.git_run import GitError
from gerrit_workflow_tools.core.review_checkout import (
    BRANCH_PREFIX,
    CheckoutOptions,
    ReviewMode,
    ReviewState,
    ReviewUsageError,
    close_review,
    find_review,
    open_review,
    set_staged,
)
from gerrit_workflow_tools.core.review_delta import ReviewDelta, load_review_delta
from gerrit_workflow_tools.core.review_target import related_chain_numbers, review_neighbour
from gerrit_workflow_tools.core.reviewed_files import mark_reviewed, reviewed_files
from gerrit_workflow_tools.core.run_context import RunContext
from gerrit_workflow_tools.render.comments import drafts_elsewhere
from gerrit_workflow_tools.render.review_json import review_state_json
from gerrit_workflow_tools.render.style import shown_path
from gerrit_workflow_tools.render.wrap import say
from gerrit_workflow_tools.review_vote import vote


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the command-line parser for ``ger review``."""
    p = argparse.ArgumentParser(
        prog="ger review",
        usage=(
            "ger review CHANGEISH [--patchset N | --bottom] [--stage | --no-stage] [--in-worktree | --in-place] "
            "[--force]\n"
            "       ger review (--next | --prev) [--stage | --no-stage] [--force]\n"
            "       ger review (--stage | --unstage | --status | --done)\n"
            "       ger review (--seen PATH ... | --seen --all | --unseen PATH ...)\n"
            "       ger review --vote [LABEL]+N ... [--chain | --change CHANGEISH ...] [-m MSG | -F FILE] [--dry-run] "
            "[-y]"
        ),
        description=(
            "Check a change out to review it, with its diff staged so every git tool shows what it changed. "
            "Walk its review chain and publish your drafts with a vote."
        ),
    )
    p.add_argument(
        "changeish", nargs="?", metavar="CHANGEISH", help=changeish_help("The change to review", change_only=True)
    )
    add_patchset_arg(p)
    p.add_argument(
        "--bottom",
        action="store_true",
        help="Start at the bottom of CHANGEISH's review chain (its lowest open change); walk up with --next.",
    )
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
    seen = p.add_argument_group(
        "files seen",
        "Gerrit's per-file reviewed flag of the patchset under review, as Gerrit's web UI and VS Code tick it.",
    )
    seen.add_argument("--seen", nargs="*", metavar="PATH", help="Mark these files of the review seen.")
    seen.add_argument("--unseen", nargs="+", metavar="PATH", help="Take the mark off these files.")
    seen.add_argument("--all", action="store_true", help="With --seen: every file of the change under review.")
    vote = p.add_argument_group(
        "voting",
        "--vote publishes all your drafts on the change with the vote, and -m (or -F) as its message; to publish "
        "only some, or to post a message without a vote: ger reply --publish.",
    )
    add_vote_arg(vote)
    add_change_option(vote, "With --vote: vote on this change; repeat for more.", repeatable=True)
    vote.add_argument(
        "--chain",
        action="store_true",
        help="With --vote: vote on every open change of the chain under review, or of the one --change.",
    )
    add_dry_run_arg(vote, scope="--vote")
    add_yes_arg(vote, scope="--vote")
    add_message_args(p)
    add_output_format_args(p)
    add_color_args(p)
    add_verbose_and_debug_log_args(p)
    return p


def _check_args(p: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    opens = args.changeish is not None or args.next or args.prev
    marks = args.seen is not None or args.unseen is not None
    if args.all and args.seen is None:
        p.error("--all goes with --seen")
    if args.seen is not None and args.unseen is not None:
        p.error("give one of --seen and --unseen")
    if args.seen is not None and bool(args.seen) == args.all:
        p.error("--seen takes the files' paths, or --all")
    if marks and (opens or args.vote is not None or args.status or args.done or args.stage is not None):
        p.error("--seen and --unseen act on the change under review; give them on their own")
    if args.vote is None and (args.change or args.chain or args.message or args.file or args.dry_run or args.yes):
        p.error("--change, --chain, -m, -F, --dry-run and -y belong to --vote")
    if args.message is not None and args.file is not None:
        p.error("give the message with one of -m or -F")
    if args.chain and args.change and len(args.change) > 1:
        p.error("--chain votes on the chain under review, or on the chain of one --change: not of several")
    if args.vote is not None and (opens or args.status or args.done or args.stage is not None):
        p.error("--vote acts on the change being reviewed; give it on its own")
    if args.changeish is not None and (args.next or args.prev or args.status or args.done):
        p.error("name a CHANGEISH or walk with --next/--prev, not both")
    if args.bottom and (args.changeish is None or args.patchset is not None):
        p.error("--bottom goes with a CHANGEISH, without --patchset: it starts at the chain's lowest open change")
    if not opens and (args.patchset is not None or args.mode is not None or args.force):
        p.error("--patchset, --in-worktree, --in-place and --force go with a CHANGEISH or --next/--prev")
    if not opens and not marks and args.vote is None and not args.status and not args.done and args.stage is None:
        p.error("name a CHANGEISH to review (or --next, --prev, --stage, --unstage, --status, --vote, --done)")


def _mode(args: argparse.Namespace, cwd: Path, settings: Settings) -> ReviewMode:
    """The checkout mode: the flag, ``ger.reviewCheckout``, or ask once and remember."""
    if args.mode is not None:
        return args.mode  # type: ignore[no-any-return]
    if settings.review_checkout is not None:
        return settings.review_checkout  # type: ignore[return-value]
    if not may_prompt(args):
        return "worktree"
    answer: ReviewMode = ask_choice(
        "Check out reviews in a separate worktree (w, default) or in this checkout (b)? ",
        {"": "worktree", "w": "worktree", "b": "branch"},
        on_eof="worktree",
    )
    apply_config_write(cwd, ConfigWrite("ger.reviewCheckout", "set", answer))
    print(f"saved: git config ger.reviewCheckout {answer}", file=sys.stderr)
    return answer


def _since_reviewed(service: GerritService, cwd: Path, state: ReviewState) -> ReviewDelta | None:
    """What changed since the patchset you last reviewed, when that is an older one; else ``None``."""
    if state.patch_set <= 1:
        return None  # nothing older to have reviewed: no second read of the change
    try:
        delta = load_review_delta(service, cwd, f"change:{state.number}", upto=state.patch_set)
    except (GerritApiError, GitError):
        return None  # a hint, not the review: never fail opening it
    return delta if delta.base is not None and not delta.up_to_date else None


def _delta_lines(delta: ReviewDelta) -> list[str]:
    files = ", ".join(
        f"{f.path} ({'binary' if f.insertions is None else f'+{f.insertions} -{f.deletions}'})" for f in delta.files
    )
    rebased = ", rebased" if delta.rebased else ""
    return [
        f"  since your review of patchset {delta.reviewed_patch_set}{rebased}: {files or 'no file changes'}",
        f"    git diff {delta.base[:12] if delta.base else ''} {delta.commit[:12]}",
    ]


def next_steps(state: ReviewState, chain: list[int] | None, cwd: Path) -> str:
    """The steps of the review loop that fit where you are, two or three: get to the worktree; below
    the top of a chain, comment and walk on; at the top (or a single change), vote and finish."""
    worktree = Path(state.worktree)
    here = cwd.resolve()
    steps = (
        [] if here == worktree.resolve() or worktree.resolve() in here.parents else [f"cd {shown_path(worktree, cwd)}"]
    )
    if chain is not None and chain[-1:] in ([state.number], []):  # the top, or no chain: nowhere to walk
        steps += ["ger review --vote +1" + (" --chain" if len(chain) > 1 else ""), "ger review --done"]
    else:
        steps += ["ger comment PATH:LINE -m …", "ger review --next"]
    return "next: " + " · ".join(steps)


def _emit_state(
    state: ReviewState,
    *,
    chain: list[int] | None,
    url: str | None,
    as_json: bool,
    delta: ReviewDelta | None = None,
    hint: str | None = None,
) -> None:
    if as_json:
        payload = review_state_json(state, chain=chain, url=url)
        if delta is not None:
            payload["since_reviewed"] = {
                "patch_set": delta.reviewed_patch_set,
                "base": delta.base,
                "rebased": delta.rebased,
                "files": [asdict(f) for f in delta.files],
            }
        print(json.dumps(payload, indent=2))
        return
    staged = " (staged)" if state.staged else ""
    print(f'reviewing {state.number} patchset {state.patch_set} "{state.change.subject}"{staged}')
    print(f"  {'worktree' if state.mode == 'worktree' else 'checkout'}: {state.worktree}")
    if chain and len(chain) > 1:
        print("  chain: " + " <- ".join(f"[{n}]" if n == state.number else str(n) for n in chain))
    if url:
        print(f"  {url}")
    for line in _delta_lines(delta) if delta else ():
        print(line)
    if hint:
        say(hint, file=sys.stdout)


def _open(args: argparse.Namespace, service: GerritService, cwd: Path, state: ReviewState | None) -> int:
    settings = service.settings
    chain: list[int] | None = None
    if args.changeish is not None:
        target, url = resolve_patchset(service, cwd, args.changeish, args.patchset)
        chain = related_chain_numbers(service, target.key, target.patch_set) if args.bottom else None
        if chain and chain[0] != target.number:
            target, url = resolve_patchset(service, cwd, f"change:{chain[0]}", None)
        mode = _mode(args, cwd, settings)
    else:
        if state is None:
            raise ReviewUsageError("no review going on here: name a CHANGEISH first")
        target, url, chain = review_neighbour(service, cwd, state, 1 if args.next else -1)
        mode = state.mode
    stage = settings.review_stage if args.stage is None else args.stage
    options = CheckoutOptions(mode=mode, stage=stage, force=args.force)
    new, moved_from = open_review(cwd, target, settings=settings, options=options)
    if moved_from is not None:
        print(f"moved {new.branch} off your commits; its old tip was {moved_from}", file=sys.stderr)
    chain = chain or related_chain_numbers(service, new.key, new.patch_set)
    delta = _since_reviewed(service, cwd, new)
    _emit_state(new, chain=chain, url=url, as_json=args.json_, delta=delta, hint=next_steps(new, chain, cwd))
    return int(ExitCode.OK)


def _mark_seen(args: argparse.Namespace, service: GerritService, state: ReviewState) -> int:
    """``--seen`` / ``--unseen``: Gerrit's reviewed flag on files of the patchset under review."""
    files = [f.path for f in commit_diff(state.worktree, state.commit).files]
    wanted = files if args.all else [p.removeprefix("./") for p in (args.seen or args.unseen)]
    unknown = [p for p in wanted if p not in files]
    if unknown:
        where = f"{state.number} patchset {state.patch_set}"
        raise ReviewUsageError(f"not a file of {where}: {', '.join(unknown)} (its files: {', '.join(files)})")
    flag = args.unseen is None
    marked = mark_reviewed(service.rest, state.number, state.patch_set, wanted, flag)
    seen = [p for p in reviewed_files(service.rest, state.number, state.patch_set) if p in files]
    left = [p for p in files if p not in seen]
    if args.json_:
        payload = {"change": state.number, "patch_set": state.patch_set, "reviewed": flag, "marked": marked}
        print(json.dumps({**payload, "seen": seen, "files": files}, indent=2))
        return int(ExitCode.OK)
    print(f"{'seen' if flag else 'not seen'} on {state.number} patchset {state.patch_set}: {', '.join(marked)}")
    print(f"  {len(seen)} of {len(files)} files seen" + (f"; not yet: {', '.join(left)}" if left else ""))
    say(
        "next: ger review --vote +1 · ger review --next" if not left else f"next: ger review --seen {left[0]}",
        file=sys.stdout,
    )
    return int(ExitCode.OK)


def _drafts_left(run: RunContext, state: ReviewState) -> dict[int, int]:
    """Your unpublished drafts on the changes this review visited; nobody sees them until published.

    A warning, never a reason to stop: without Gerrit, or when it fails, there are none to tell.
    """
    numbers = [
        int(b.removeprefix(BRANCH_PREFIX)) for b in state.created_branches if b.removeprefix(BRANCH_PREFIX).isdigit()
    ]
    if not run.gerrit.configured:
        return {}
    try:
        return run.gerrit.comments.draft_counts(sorted(set(numbers) | {state.number}))
    except (GerritApiError, ConfigError):
        return {}


def main(argv: list[str] | None = None, *, gerrit: GerritRest | None = None) -> int:
    """Check out, stage, walk, vote on or leave a review."""
    return run_cli_command(lambda: _run(argv, gerrit=gerrit))


def _run(argv: list[str] | None, *, gerrit: GerritRest | None) -> int:
    p = _build_parser()
    args = p.parse_args(argv)
    _check_args(p, args)
    return _act(args, p, init_cli_runtime(args, gerrit=gerrit))


def _act(args: argparse.Namespace, p: argparse.ArgumentParser, run: RunContext) -> int:
    cwd = run.cwd
    state = find_review(cwd, run.settings)
    if args.status:
        if state is None:
            print(json.dumps({"review": None}) if args.json_ else "no review going on")
        else:
            _emit_state(state, chain=None, url=None, as_json=args.json_, hint=next_steps(state, None, cwd))
        return int(ExitCode.OK)
    if args.done:
        if state is None:
            raise ReviewUsageError("no review going on here")
        deleted, kept = close_review(state)
        left = _drafts_left(run, state)
        if args.json_:
            payload = {"done": state.number, "deleted": deleted, "kept": kept, "drafts": left}
            print(json.dumps(payload, indent=2))
        else:
            print(f"done reviewing {state.number}; deleted {', '.join(deleted) or 'no branches'}")
            for branch in kept:
                print(f"  kept {branch}: it has commits of yours")
        if left and not args.json_:
            sys.stdout.flush()  # after what was done, also when both go to one pipe
            print(f"warning: {drafts_elsewhere(left)}", file=sys.stderr)
        return int(ExitCode.OK)
    if args.seen is not None or args.unseen is not None:
        if state is None:
            raise ReviewUsageError("no review going on here: name a CHANGEISH first")
        return _mark_seen(args, run.gerrit, state)
    if args.vote is None and args.changeish is None and not args.next and not args.prev:
        if state is None:
            raise ReviewUsageError("no review going on here: name a CHANGEISH first")
        _emit_state(set_staged(state, bool(args.stage)), chain=None, url=None, as_json=args.json_)
        return int(ExitCode.OK)
    service = run.gerrit
    if args.vote is not None:
        return vote(args, p, service, cwd)
    return _open(args, service, cwd, state)


if __name__ == "__main__":
    raise SystemExit(main())
