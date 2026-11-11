"""CLI for ``ger checkout``: check a Gerrit change out on a branch that tracks its target branch."""

from __future__ import annotations

import argparse
import sys

from gerrit_workflow_tools.cli_common import (
    ExitCode,
    add_color_args,
    add_verbose_and_debug_log_args,
    changeish_help,
    confirm,
    init_cli_runtime,
    print_note,
    print_resolution_note,
    run_cli_command,
)
from gerrit_workflow_tools.core.change_checkout import (
    CheckoutAction,
    CheckoutPlan,
    CheckoutRefusal,
    CheckoutResult,
    LocalState,
    apply_checkout,
    choose_action,
    plan_checkout,
)
from gerrit_workflow_tools.core.gerrit.rest import GerritRest
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.gerrit_patchset import resolve_change
from gerrit_workflow_tools.core.rebase_state import rebase_refusal
from gerrit_workflow_tools.core.upstream_interactive import stdin_is_terminal
from gerrit_workflow_tools.core.usage import UsageError


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the command-line parser for ``ger checkout``."""
    p = argparse.ArgumentParser(
        prog="ger checkout",
        usage="ger checkout CHANGEISH [--patchset N] [-b NAME] [--update | --no-update] [--force]",
        description=(
            "Check a Gerrit change out on a local branch whose upstream is the change's target branch, "
            "so ger log and ger push work on it. A local version of the change is switched to, or updated."
        ),
    )
    p.add_argument("changeish", metavar="CHANGEISH", help=changeish_help("The change", change_only=True))
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
        help="Update an older patchset of the change to this one without asking.",
    )
    update.add_argument(
        "--no-update", dest="update", action="store_false", help="Switch to the local version as it is."
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Replace a local version Gerrit never had, or reset an existing branch NAME.",
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


def _report(plan: CheckoutPlan, result: CheckoutResult, url: str | None) -> None:
    patchset, branch = plan.patchset, result.branch
    change = f'change {patchset.number} patchset {patchset.patch_set} "{patchset.subject}"'
    where = "staying on" if result.unchanged else "switched to"
    if result.action is CheckoutAction.CREATE:
        print(f"checked out {change} on {branch}")
    elif result.action is CheckoutAction.UPDATE:
        rebased = f", rebased the {result.rebased} commit(s) above it" if result.rebased else ""
        old = plan.local.patch_set if plan.local else None
        verb = "moved" if old and old > patchset.patch_set else "updated"  # back to an older patchset
        print(f"{verb} {branch} {f'from patchset {old} ' if old else ''}to {change}{rebased}")
    elif plan.state is LocalState.SAME:
        print(f"{'already on' if result.unchanged else 'switched to'} {branch}: it has {change}")
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
    cwd, settings, _ = init_cli_runtime(args)
    if refusal := rebase_refusal(cwd):
        raise UsageError(refusal)
    service = GerritService.from_cwd(cwd, settings=settings, rest=gerrit)
    change = resolve_change(service, cwd, args.changeish)
    print_resolution_note(change.resolution)
    try:
        plan = plan_checkout(cwd, change, args.patchset, settings=settings, branch=args.branch)
        action = choose_action(plan, update=args.update, force=args.force) or _ask_update(plan)
        result = apply_checkout(cwd, plan, action, settings=settings)
    except CheckoutRefusal as refusal:
        print(f"error: {refusal}", file=sys.stderr)
        return int(ExitCode.ATTENTION)
    _report(plan, result, change.view.url(service.web_base))
    return int(ExitCode.OK)


if __name__ == "__main__":
    raise SystemExit(main())
