"""Doing what ``ger checkout`` decided (``core/change_checkout.py``): moving the **local version** onto
the patchset, replaying the commits above it, merging your edits into it; and bringing every
outdated commit of the local stack up to date (:func:`update_stack`).

Spec: docu/spec/commands/checkout.md. Refuses before anything moves; nothing here prints or prompts.
"""

from __future__ import annotations

from pathlib import Path

from gerrit_workflow_tools.core.change_checkout import (
    CheckoutAction,
    CheckoutPlan,
    CheckoutRefusal,
    CheckoutResult,
    LocalState,
    gerrit_branch,
    plan_checkout,
)
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.change_view import ChangeView
from gerrit_workflow_tools.core.gerrit.changes import ChangeApi
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.gerrit.stack_context import build_triplet
from gerrit_workflow_tools.core.gerrit_change_status import PatchsetStatus, patchset_status
from gerrit_workflow_tools.core.gerrit_patchset import fetch_patchset, resolve_change
from gerrit_workflow_tools.core.git_run import git, git_out
from gerrit_workflow_tools.core.git_state import checked_out_branch_name, resolve_upstream_abbrev_ref
from gerrit_workflow_tools.core.patchset_history import record_taken
from gerrit_workflow_tools.core.reorder_preview import PickConflict, PreviewUnavailable, first_conflict
from gerrit_workflow_tools.core.review_checkout import refuse_own_edits
from gerrit_workflow_tools.core.stack import get_stack_snapshot, merge_commits
from gerrit_workflow_tools.core.upstream import track_remote_branch


def _refusal_to_rebase(plan: CheckoutPlan, why: str) -> CheckoutRefusal:
    local, patchset = plan.local, plan.patchset
    assert local is not None
    return CheckoutRefusal(
        f"your {len(local.above)} commit(s) above change {patchset.number} on {plan.branch} do not apply onto "
        f"patchset {patchset.patch_set} ({why}); nothing was changed. Rebase them yourself "
        f"(git rebase --onto {patchset.commit[:12]} {local.commit.short_sha} {plan.branch}), "
        f"or check patchset {patchset.patch_set} out on a branch of its own with -b NAME"
    )


def _refuse_merge_above(cwd: Path | str, plan: CheckoutPlan) -> None:
    """Refuse to move a local version that has a merge commit above it.

    ``git rebase`` does not replay a merge: it leaves it out, and with it whatever only the merge
    records (a conflict resolved by hand, an edit made while merging). The preview cannot tell
    either: it replays the merge's first-parent diff, which the rebase then never applies.
    """
    local, patchset = plan.local, plan.patchset
    assert local is not None
    merges = merge_commits(cwd, [commit.sha for commit in local.above])
    merge = next((commit for commit in local.above if commit.sha in merges), None)
    if merge is None:
        return
    raise CheckoutRefusal(
        f'"{merge.subject}" ({merge.short_sha}), above change {patchset.number} on {plan.branch}, is a merge commit, '
        f"which git rebase cannot replay onto patchset {patchset.patch_set}: what only the merge records would be "
        "lost; nothing was changed. Rebase the commits above the change yourself "
        f"(git rebase --rebase-merges --onto {patchset.commit[:12]} {local.commit.short_sha} {plan.branch}), "
        f"or check patchset {patchset.patch_set} out on a branch of its own with -b NAME"
    )


def _merge_conflict(plan: CheckoutPlan, files: list[str]) -> CheckoutRefusal:
    """Why the merge is refused, and how to do it by hand: the force path puts the patchset in your history."""
    local, patchset, edit_base = plan.local, plan.patchset, plan.edit_base
    assert local is not None and edit_base is not None
    mine, theirs, ps, change = local.commit.short_sha, patchset.commit[:12], patchset.patch_set, patchset.number
    paths = f" -- {' '.join(files)}" if files else ""
    return CheckoutRefusal(
        f"your edits ({mine}) and patchset {ps} of change {change} ({theirs}) change the same lines "
        f"({', '.join(files) or 'files'}); nothing was changed.\n"
        f"  see where: git diff {mine} {theirs}{paths}\n"
        f"  merge them by hand: ger checkout change:{change} --force (takes patchset {ps}, prints your old tip), "
        f"then git diff {edit_base[1][:12]} {mine} | git apply -3, resolve, git commit -a --amend --no-edit\n"
        f"  or keep yours and replace patchset {ps}: ger push --force"
    )


def _merged_commit(cwd: Path | str, plan: CheckoutPlan) -> str:
    """One commit: the patchset with your edits of the local version merged in (base: :attr:`CheckoutPlan.edit_base`).

    It carries the local version's message and author and sits where the patchset sits, so it
    pushes as the next patchset; when that is the patchset's tree and message, it is the patchset
    itself. Nothing is written but objects: a conflict refuses, unchanged.
    """
    local, patchset, edit_base = plan.local, plan.patchset, plan.edit_base
    assert local is not None and edit_base is not None
    base = edit_base[1]
    merged = git(
        "merge-tree",
        "--write-tree",
        "--name-only",
        f"--merge-base={base}",
        patchset.commit,
        local.commit.sha,
        cwd=cwd,
        check=False,
    )
    lines = merged.stdout.splitlines()
    if merged.returncode != 0 or not lines:
        conflicted = lines[1 : lines.index("")] if "" in lines else lines[1:]
        raise _merge_conflict(plan, conflicted)
    fields = git_out("log", "-1", "--format=%an%x00%ae%x00%ad%x00%B", local.commit.sha, cwd=cwd).split("\x00", 3)
    name, email, date, message = fields
    if git_out("log", "-1", "--format=%T%x00%B", patchset.commit, cwd=cwd) == f"{lines[0]}\x00{message}".rstrip():
        return patchset.commit  # your version holds nothing it does not: take Gerrit's commit, nothing to push
    parents = git_out("rev-list", "--parents", "-n", "1", patchset.commit, cwd=cwd).split()[1:2]
    env = {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_AUTHOR_DATE": date}
    argv = ["commit-tree", lines[0], *(arg for parent in parents for arg in ("-p", parent))]
    return git(*argv, cwd=cwd, env=env, input=message).stdout.strip()


def _update(cwd: Path | str, plan: CheckoutPlan, target: str | None = None) -> tuple[int, int]:
    """Move the local version to *target* (default: the patchset), replaying the commits above it.

    When it becomes the patchset, the commits above it that :attr:`CheckoutPlan.gerrit_above` names
    become Gerrit's patchsets themselves, and only the rest are replayed. Returns ``(taken, replayed)``.
    """
    local = plan.local
    target = target or plan.patchset.commit
    assert local is not None
    taken = plan.gerrit_above if target == plan.patchset.commit else ()
    base = taken[-1].commit if taken else target
    below, rest = (local.commit, *local.above)[len(taken)], local.above[len(taken) :]
    if not rest:
        git("switch", "-q", "-C", plan.branch, base, cwd=cwd)
        return len(taken), 0
    try:
        conflict: PickConflict | None = first_conflict(cwd, base, rest, (local.commit, *local.above))
    except PreviewUnavailable:
        conflict = None  # this git cannot tell beforehand: the rebase itself finds out
    if conflict is not None:
        files = ", ".join(conflict.files)
        raise _refusal_to_rebase(plan, f'"{conflict.commit.subject}" conflicts' + (f" in {files}" if files else ""))
    done = git("rebase", "-q", "--onto", base, below.sha, plan.branch, cwd=cwd, check=False)
    if done.returncode != 0:
        git("rebase", "--abort", cwd=cwd, check=False)
        if plan.current_branch and plan.current_branch != plan.branch:
            git("switch", "-q", plan.current_branch, cwd=cwd, check=False)
        raise _refusal_to_rebase(plan, (done.stderr.strip() or "git rebase failed").partition("\n")[0])
    return len(taken), len(rest)


def apply_checkout(
    cwd: Path | str, plan: CheckoutPlan, action: CheckoutAction, *, settings: Settings
) -> CheckoutResult:
    """Do *action*. Refuses before touching anything while the working tree has changes to tracked files,
    or when a merge commit sits above the local version to move (:func:`_refuse_merge_above`).

    The patchset is fetched with its target branch, so a branch created for it tracks an
    up-to-date ``<remote>/<target>``. A local version without a Gerrit destination gets that
    upstream too; one that has a destination keeps it.
    """
    remote, patchset, branch = settings.gerrit_remote, plan.patchset, plan.branch
    if action is CheckoutAction.SWITCH and branch == plan.current_branch:
        return CheckoutResult(action, branch, resolve_upstream_abbrev_ref(cwd, branch), unchanged=True)
    refuse_own_edits(cwd, None, rerun="ger checkout")
    if action in (CheckoutAction.UPDATE, CheckoutAction.MERGE):
        _refuse_merge_above(cwd, plan)
    rebased, taken, moved_from, merged_from, same = 0, 0, None, None, False
    if action is CheckoutAction.SWITCH:
        git("switch", "-q", branch, cwd=cwd)
    else:
        above = plan.gerrit_above if action is not CheckoutAction.CREATE else ()
        fetch_patchset(cwd, remote, patchset, with_target_branch=True, also=above)
    if action is CheckoutAction.CREATE:
        old = plan.existing_tip
        if old and git_out("rev-list", old, "--not", patchset.commit, cwd=cwd):
            moved_from = old
        git("switch", "-q", "-C" if old else "-c", branch, patchset.commit, cwd=cwd)
    elif action is CheckoutAction.UPDATE:
        local = plan.local
        assert local is not None
        if plan.state is LocalState.EDITED:
            moved_from = (local.above[-1] if local.above else local.commit).sha
        taken, rebased = _update(cwd, plan)
    elif action is CheckoutAction.MERGE:
        merged = _merged_commit(cwd, plan)
        taken, rebased = _update(cwd, plan, merged)
        same = merged == patchset.commit
        if not same:  # built on the patchset, though not its child: ADR-0015
            why = f"ger checkout: change {patchset.number} patchset {patchset.patch_set} merged into {merged[:12]}"
            record_taken(cwd, patchset.commit, merged, why=why)
        merged_from = plan.edit_base[0] if plan.edit_base else None
    if action is CheckoutAction.CREATE or gerrit_branch(cwd, branch, settings) is None:
        track_remote_branch(cwd, branch, remote, patchset.target_branch)
    upstream = resolve_upstream_abbrev_ref(cwd, branch)
    return CheckoutResult(
        action,
        branch,
        upstream,
        rebased=rebased,
        moved_from=moved_from,
        merged_from=merged_from,
        took=plan.gerrit_above[:taken],
        same_as_patchset=same,
    )


def update_stack(
    service: GerritService, cwd: Path | str, *, settings: Settings
) -> list[tuple[CheckoutPlan, CheckoutResult]]:
    """``ger checkout --update`` without a change: every outdated commit of the local stack to its current patchset.

    The lowest outdated commit is updated like ``ger checkout change:N --update``, which takes the
    commits above it Gerrit has on top of the new patchset as they are; then the next one, until none
    is left, or one did not move (its update was refused, or it is no longer the branch's). An
    outdated commit holds no edits of yours, so nothing is lost. Each step is one plan and result.
    """
    branch = checked_out_branch_name(cwd)
    if branch is None:
        raise CheckoutRefusal("HEAD is detached: switch to the branch whose stack to update, or name a change")
    done: list[tuple[CheckoutPlan, CheckoutResult]] = []
    tried: set[int] = set()
    changes = service.changes.exact()
    while (number := _lowest_outdated(service, changes, cwd, settings=settings)) is not None and number not in tried:
        tried.add(number)
        change = resolve_change(service, cwd, f"change:{number}")
        plan = plan_checkout(cwd, change, None, settings=settings, branch=branch, changes=changes)
        if plan.state is not LocalState.PUSHED:
            break
        done.append((plan, apply_checkout(cwd, plan, CheckoutAction.UPDATE, settings=settings)))
    return done


def _lowest_outdated(service: GerritService, changes: ChangeApi, cwd: Path | str, *, settings: Settings) -> int | None:
    """The change number of the lowest commit of the local stack Gerrit has a newer patchset of, if any."""
    stack, rows = service.stack_context(), get_stack_snapshot(cwd, settings=settings).commits
    keys = {
        r.sha: build_triplet(stack.project, stack.push_branch, r.valid_change_id) for r in rows if r.valid_change_id
    }
    payloads = changes.get_payloads(list(keys.values())) if keys else {}
    for row in rows:
        payload = payloads.get(keys.get(row.sha, ""))
        view = ChangeView.from_payload(payload) if payload else None
        if view is not None and view.is_open and patchset_status(row.sha, view) is PatchsetStatus.OUTDATED:
            return view.number
    return None
