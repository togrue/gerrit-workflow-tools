"""CLI for ``ger checkout``: check a Gerrit change out on a branch that tracks its target branch."""

from __future__ import annotations

import argparse
import sys

from gerrit_workflow_tools.cli_common import (
    ExitCode,
    add_color_args,
    add_verbose_and_debug_log_args,
    assume_yes,
    changeish_help,
    confirm,
    init_cli_runtime,
    run_cli_command,
)
from gerrit_workflow_tools.cli_notes import print_note, print_resolution_note
from gerrit_workflow_tools.cli_terminal import stdin_is_terminal
from gerrit_workflow_tools.core.change_checkout import (
    CheckoutAction,
    CheckoutPlan,
    CheckoutRefusal,
    CheckoutResult,
    LocalState,
    choose_action,
    plan_checkout,
)
from gerrit_workflow_tools.core.change_checkout_apply import apply_checkout, update_stack
from gerrit_workflow_tools.core.gerrit.rest import GerritRest
from gerrit_workflow_tools.core.gerrit_patchset import resolve_change
from gerrit_workflow_tools.core.rebase_state import rebase_refusal
from gerrit_workflow_tools.core.run_context import RunContext
from gerrit_workflow_tools.core.usage import UsageError


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the command-line parser for ``ger checkout``."""
    p = argparse.ArgumentParser(
        prog="ger checkout",
        usage="ger checkout CHANGEISH [--patchset N] [-b NAME] [--update | --no-update] [--force] [-y]\n"
        "       ger checkout --update",
        description=(
            "Check a Gerrit change out on a local branch whose upstream is the change's target branch, "
            "so ger log and ger push work on it. A local version of the change is switched to, or updated: "
            "a branch whose tip is the change wins; else ger checkout goes to (or stays on) the branch that "
            "holds the change and says how many commits below HEAD it is. Without CHANGEISH, --update brings "
            "every outdated commit of the local stack to Gerrit's current patchset."
        ),
    )
    p.add_argument(
        "changeish",
        metavar="CHANGEISH",
        nargs="?",
        help=changeish_help("The change", change_only=True) + " Leave it out with --update: the whole local stack.",
    )
    p.add_argument("--patchset", type=int, default=None, metavar="N", help="Check out patchset N (default: current).")
    p.add_argument(
        "-b",
        "--branch",
        default=None,
        metavar="NAME",
        help="Put it on branch NAME (default: its local version's branch, else change-<number>).",
    )
    update = p.add_mutually_exclusive_group()
    update.add_argument(
        "--update",
        dest="update",
        action="store_true",
        default=None,
        help=(
            "Update an older patchset of the change to this one without asking; a local version Gerrit "
            "never had gets the patchset merged in (a conflict changes nothing)."
        ),
    )
    update.add_argument(
        "--no-update", dest="update", action="store_false", help="Switch to the local version as it is."
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Replace a local version Gerrit never had, or reset an existing branch NAME.",
    )
    p.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Answer the update question with yes, as --update does (GER_ASSUME_YES=1 too).",
    )
    add_color_args(p)
    add_verbose_and_debug_log_args(p, debug_log_help="Log git and Gerrit calls to stderr.")
    return p


def _ask_update(plan: CheckoutPlan) -> CheckoutAction:
    """Ask whether to update a pushed local version; off a terminal, refuse and name the flags."""
    local, patchset = plan.local, plan.patchset
    assert local is not None
    have = f"{plan.branch} has patchset {local.patch_set} of change {patchset.number}"
    if not stdin_is_terminal():
        raise CheckoutRefusal(
            f"{have}, Gerrit has patchset {patchset.patch_set}: pass --update to update it, "
            "or --no-update to switch to it as it is"
        )
    rebase = f" and rebase the {len(local.above)} commit(s) above it" if local.above else ""
    question = f"{have}. Update it to patchset {patchset.patch_set}{rebase}?"
    return CheckoutAction.UPDATE if confirm(question, default=True) else CheckoutAction.SWITCH


def _above(result: CheckoutResult) -> str:
    """What became of the commits above: taken as Gerrit has them, rebased."""
    took = ", ".join(f"change {p.number} patchset {p.patch_set}" for p in result.took)
    parts = [f"took {took} above it as Gerrit has it"] if took else []
    parts += [f"rebased the {result.rebased} commit(s) above it"] if result.rebased else []
    return "".join(f", {part}" for part in parts)


def _report(plan: CheckoutPlan, result: CheckoutResult, url: str | None) -> None:
    patchset, branch = plan.patchset, result.branch
    change = f'change {patchset.number} patchset {patchset.patch_set} "{patchset.subject}"'
    where = "staying on" if result.unchanged else "switched to"
    if result.action is CheckoutAction.CREATE:
        print(f"checked out {change} on {branch}")
    elif result.action is CheckoutAction.UPDATE:
        old = plan.local.patch_set if plan.local else None
        verb = "moved" if old and old > patchset.patch_set else "updated"  # back to an older patchset
        print(f"{verb} {branch} {f'from patchset {old} ' if old else ''}to {change}{_above(result)}")
    elif result.action is CheckoutAction.MERGE and result.same_as_patchset:
        print(f"your version on {branch} has nothing {change} lacks: took it as Gerrit has it{_above(result)}")
    elif result.action is CheckoutAction.MERGE:
        base = f" (your edits against patchset {result.merged_from})" if result.merged_from else ""
        print(f"merged your version on {branch} into {change}{base}{_above(result)}; push it as the next patchset")
    elif plan.state is LocalState.SAME:
        above = len(plan.local.above) if plan.local else 0  # the branch tip, where HEAD is now
        where_it_is = f"{change} is {above} commit(s) below HEAD" if above else f"it has {change}"
        print(f"{'already on' if result.unchanged else 'switched to'} {branch}: {where_it_is}")
    elif plan.state is LocalState.PUSHED:
        old = plan.local.patch_set if plan.local else None
        print(f"{where} {branch}, left at patchset {old} (Gerrit has patchset {patchset.patch_set})")
    else:
        print(f"{where} {branch}, left as it is: your version differs from patchset {patchset.patch_set}")
    if result.upstream:
        print(f"  upstream: {result.upstream}")
    if url:
        print(f"  {url}")
    if result.moved_from:
        print(f"moved {branch} off commits Gerrit does not have; its old tip was {result.moved_from}", file=sys.stderr)
    for other in plan.elsewhere:
        print_note(f"note: change {patchset.number} also has a local version on {other.branch}")


def main(argv: list[str] | None = None, *, gerrit: GerritRest | None = None) -> int:
    """Check a Gerrit change out on a branch tracking its target branch."""
    return run_cli_command(lambda: _run(argv, gerrit=gerrit))


def _run(argv: list[str] | None, *, gerrit: GerritRest | None) -> int:
    args = _build_parser().parse_args(argv)
    run = init_cli_runtime(args, gerrit=gerrit)
    cwd, settings = run.cwd, run.settings
    if refusal := rebase_refusal(cwd):
        raise UsageError(refusal)
    service = run.gerrit
    if args.changeish is None:
        return _update_stack(args, run)
    change = resolve_change(service, cwd, args.changeish)
    print_resolution_note(change.resolution)
    changes = service.changes.exact()
    plan = plan_checkout(cwd, change, args.patchset, settings=settings, branch=args.branch, changes=changes)
    update = True if args.update is None and (args.yes or assume_yes()) else args.update
    action = choose_action(plan, update=update, force=args.force) or _ask_update(plan)
    result = apply_checkout(cwd, plan, action, settings=settings)
    _report(plan, result, change.view.url(service.web_base))
    return int(ExitCode.OK)


def _update_stack(args: argparse.Namespace, run: RunContext) -> int:
    """``ger checkout --update`` alone: every outdated commit of the local stack, lowest first."""
    if not args.update or args.patchset is not None or args.branch is not None or args.force:
        raise UsageError(
            "name the change to check out (CHANGEISH); without one, only --update is accepted: "
            "it brings every outdated commit of the local stack to Gerrit's current patchset"
        )
    steps = update_stack(run.gerrit, run.cwd, settings=run.settings)
    if not steps:
        print(f"nothing to update: no commit of {run.worktree.branch or 'HEAD'} is outdated")
    for plan, result in steps:
        _report(plan, result, None)
    return int(ExitCode.OK)


if __name__ == "__main__":
    raise SystemExit(main())
