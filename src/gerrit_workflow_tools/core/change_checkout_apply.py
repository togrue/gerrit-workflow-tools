"""Doing what ``ger checkout`` decided (``core/change_checkout.py``): moving the **local version** onto
the patchset, replaying the commits above it, merging your edits into it.

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
)
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit_patchset import fetch_patchset
from gerrit_workflow_tools.core.git_run import git, git_out
from gerrit_workflow_tools.core.git_state import resolve_upstream_abbrev_ref
from gerrit_workflow_tools.core.patchset_history import record_taken
from gerrit_workflow_tools.core.reorder_preview import PickConflict, PreviewUnavailable, first_conflict
from gerrit_workflow_tools.core.review_checkout import refuse_own_edits
from gerrit_workflow_tools.core.stack import merge_commits
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
    pushes as the next patchset. Nothing is written but objects: a conflict refuses, unchanged.
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
    parents = git_out("rev-list", "--parents", "-n", "1", patchset.commit, cwd=cwd).split()[1:2]
    env = {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_AUTHOR_DATE": date}
    argv = ["commit-tree", lines[0], *(arg for parent in parents for arg in ("-p", parent))]
    return git(*argv, cwd=cwd, env=env, input=message).stdout.strip()


def _update(cwd: Path | str, plan: CheckoutPlan, target: str | None = None) -> int:
    """Move the local version to *target* (default: the patchset), replaying the commits above it; how many."""
    local = plan.local
    target = target or plan.patchset.commit
    assert local is not None
    if not local.above:
        git("switch", "-q", "-C", plan.branch, target, cwd=cwd)
        return 0
    try:
        conflict: PickConflict | None = first_conflict(cwd, target, local.above, (local.commit, *local.above))
    except PreviewUnavailable:
        conflict = None  # this git cannot tell beforehand: the rebase itself finds out
    if conflict is not None:
        files = ", ".join(conflict.files)
        raise _refusal_to_rebase(plan, f'"{conflict.commit.subject}" conflicts' + (f" in {files}" if files else ""))
    done = git("rebase", "-q", "--onto", target, local.commit.sha, plan.branch, cwd=cwd, check=False)
    if done.returncode != 0:
        git("rebase", "--abort", cwd=cwd, check=False)
        if plan.current_branch and plan.current_branch != plan.branch:
            git("switch", "-q", plan.current_branch, cwd=cwd, check=False)
        raise _refusal_to_rebase(plan, (done.stderr.strip() or "git rebase failed").partition("\n")[0])
    return len(local.above)


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
    rebased, moved_from, merged_from = 0, None, None
    if action is CheckoutAction.SWITCH:
        git("switch", "-q", branch, cwd=cwd)
    else:
        fetch_patchset(cwd, remote, patchset, with_target_branch=True)
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
        rebased = _update(cwd, plan)
    elif action is CheckoutAction.MERGE:
        merged = _merged_commit(cwd, plan)
        rebased = _update(cwd, plan, merged)
        why = f"ger checkout: change {patchset.number} patchset {patchset.patch_set} merged into {merged[:12]}"
        record_taken(cwd, patchset.commit, merged, why=why)  # built on it, though not its child: ADR-0015
        merged_from = plan.edit_base[0] if plan.edit_base else None
    if action is CheckoutAction.CREATE or gerrit_branch(cwd, branch, settings) is None:
        track_remote_branch(cwd, branch, remote, patchset.target_branch)
    upstream = resolve_upstream_abbrev_ref(cwd, branch)
    return CheckoutResult(action, branch, upstream, rebased=rebased, moved_from=moved_from, merged_from=merged_from)
