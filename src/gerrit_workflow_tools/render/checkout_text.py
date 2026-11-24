"""What ``ger checkout`` says once it checked a change out: the first line, the upstream, the URL.

The words of every outcome ``core/change_checkout.py`` can reach, as lines; the command prints them.
"""

from __future__ import annotations

from gerrit_workflow_tools.core.change_checkout import CheckoutAction, CheckoutPlan, CheckoutResult, LocalState
from gerrit_workflow_tools.core.words import counted


def _above(result: CheckoutResult) -> str:
    """What became of the commits above: taken as Gerrit has them, rebased."""
    took = ", ".join(f"change {p.number} patchset {p.patch_set}" for p in result.took)
    parts = [f"took {took} above it as Gerrit has it"] if took else []
    parts += [f"rebased the {counted(result.rebased, 'commit')} above it"] if result.rebased else []
    return "".join(f", {part}" for part in parts)


def checkout_lines(plan: CheckoutPlan, result: CheckoutResult, url: str | None) -> list[str]:
    """What became of the change on its branch, then its upstream and URL (stdout)."""
    patchset, branch = plan.patchset, result.branch
    change = f'change {patchset.number} patchset {patchset.patch_set} "{patchset.subject}"'
    where = "staying on" if result.unchanged else "switched to"
    if result.action is CheckoutAction.CREATE:
        first = f"checked out {change} on {branch}"
    elif result.action is CheckoutAction.UPDATE:
        old = plan.local.patch_set if plan.local else None
        verb = "moved" if old and old > patchset.patch_set else "updated"  # back to an older patchset
        first = f"{verb} {branch} {f'from patchset {old} ' if old else ''}to {change}{_above(result)}"
    elif result.action is CheckoutAction.MERGE and result.same_as_patchset:
        first = f"your version on {branch} has nothing {change} lacks: took it as Gerrit has it{_above(result)}"
    elif result.action is CheckoutAction.MERGE:
        base = f" (your edits against patchset {result.merged_from})" if result.merged_from else ""
        first = f"merged your version on {branch} into {change}{base}{_above(result)}; push it as the next patchset"
    elif plan.state is LocalState.SAME:
        above = len(plan.local.above) if plan.local else 0  # the branch tip, where HEAD is now
        where_it_is = f"{change} is {counted(above, 'commit')} below HEAD" if above else f"it has {change}"
        first = f"{'already on' if result.unchanged else 'switched to'} {branch}: {where_it_is}"
    elif plan.state is LocalState.PUSHED:
        old = plan.local.patch_set if plan.local else None
        first = f"{where} {branch}, left at patchset {old} (Gerrit has patchset {patchset.patch_set})"
    else:
        first = f"{where} {branch}, left as it is: your version differs from patchset {patchset.patch_set}"
    return [first, *([f"  upstream: {result.upstream}"] if result.upstream else []), *([f"  {url}"] if url else [])]
