"""CLI for ``ger checkout``: check a Gerrit change out on a branch that tracks its target branch."""

from __future__ import annotations

import argparse
import sys

from gerrit_workflow_tools.cli_args import (
    add_color_args,
    add_patchset_arg,
    add_verbose_and_debug_log_args,
    add_yes_arg,
    changeish_help,
)
from gerrit_workflow_tools.cli_common import ExitCode, init_cli_runtime, require_prompt, run_cli_command
from gerrit_workflow_tools.cli_notes import print_note, print_resolution_note
from gerrit_workflow_tools.cli_terminal import assume_yes, confirm
from gerrit_workflow_tools.core.change_checkout import (
    CheckoutAction,
    CheckoutPlan,
    CheckoutResult,
    choose_action,
    plan_checkout,
)
from gerrit_workflow_tools.core.change_checkout_apply import apply_checkout, update_stack
from gerrit_workflow_tools.core.gerrit.rest import GerritRest
from gerrit_workflow_tools.core.gerrit_patchset import resolve_change
from gerrit_workflow_tools.core.rebase_next_step import rebase_refusal
from gerrit_workflow_tools.core.run_context import RunContext
from gerrit_workflow_tools.core.usage import UsageError
from gerrit_workflow_tools.core.words import counted
from gerrit_workflow_tools.render.checkout_text import checkout_lines
from gerrit_workflow_tools.render.wrap import say


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
    add_patchset_arg(p)
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
    add_yes_arg(p)
    add_color_args(p)
    add_verbose_and_debug_log_args(p)
    return p


def _ask_update(args: argparse.Namespace, plan: CheckoutPlan) -> CheckoutAction:
    """Ask whether to update a pushed local version; off a terminal, refuse and name the flags."""
    local, patchset = plan.local, plan.patchset
    assert local is not None
    have = f"{plan.branch} has patchset {local.patch_set} of change {patchset.number}"
    require_prompt(
        args,
        instead=f"--update to update {plan.branch} from patchset {local.patch_set} to {patchset.patch_set}, "
        "or --no-update to switch to it as it is",
    )
    rebase = f" and rebase the {counted(len(local.above), 'commit')} above it" if local.above else ""
    question = f"{have}. Update it to patchset {patchset.patch_set}{rebase}?"
    return CheckoutAction.UPDATE if confirm(question, default=True) else CheckoutAction.SWITCH


def _report(plan: CheckoutPlan, result: CheckoutResult, url: str | None) -> None:
    first, *rest = checkout_lines(plan, result, url)
    say(first, file=sys.stdout)
    for line in rest:
        print(line)
    if result.moved_from:
        print(
            f"moved {result.branch} off commits Gerrit does not have; its old tip was {result.moved_from}",
            file=sys.stderr,
        )
    for other in plan.elsewhere:
        print_note(f"note: change {plan.patchset.number} also has a local version on {other.branch}")


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
    action = choose_action(plan, update=update, force=args.force) or _ask_update(args, plan)
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
