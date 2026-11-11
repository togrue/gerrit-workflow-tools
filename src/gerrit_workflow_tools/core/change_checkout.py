"""Checking a Gerrit change out to work on it: its branch, its upstream, and its **local version**.

Spec: docu/spec/commands/checkout.md. Looking, deciding and doing are apart, so the command
can ask in between: :func:`plan_checkout` only reads the repository, :func:`choose_action`
turns the user's flags into what to do (``None`` when only the user can say), and
:func:`apply_checkout` does it. Nothing here prints or prompts.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.rest import norm_change_id
from gerrit_workflow_tools.core.gerrit_patchset import GerritPatchset, ResolvedChange, fetch_patchset
from gerrit_workflow_tools.core.git_run import commit_sha_or_none, git, git_out
from gerrit_workflow_tools.core.git_state import (
    checked_out_branch_name,
    effective_gerrit_destination_branch,
    refs_for_push_branch_name,
    resolve_upstream_abbrev_ref,
)
from gerrit_workflow_tools.core.reorder_preview import PickConflict, PreviewUnavailable, first_conflict
from gerrit_workflow_tools.core.review_checkout import BRANCH_PREFIX, own_edits
from gerrit_workflow_tools.core.stack import Commit, commits_in_range, read_commit_rows
from gerrit_workflow_tools.core.upstream import track_remote_branch


class CheckoutRefusal(RuntimeError):
    """ger will not go on without the user: their edits, commits or branches are in the way (exit 1)."""


class LocalState(Enum):
    """How the change's local version relates to the patchset being checked out."""

    NONE = "none"
    """No local version: the patchset goes on a branch of its own."""
    SAME = "same"
    """The local version is that patchset."""
    PUSHED = "pushed"
    """The local version is another patchset of the change: updating it loses nothing of yours."""
    EDITED = "edited"
    """Gerrit never had the local version: it holds edits of yours."""


class CheckoutAction(Enum):
    """What ``ger checkout`` does."""

    CREATE = "create"
    """Put the patchset on a new branch (or reset an existing one, forced)."""
    SWITCH = "switch"
    """Switch to the local version as it is."""
    UPDATE = "update"
    """Move the local version to the patchset; the commits above it are rebased onto it."""


@dataclass(frozen=True)
class LocalVersion:
    """A commit carrying the change's Change-Id on a local branch, not yet on the Gerrit remote."""

    branch: str
    commit: Commit
    patch_set: int | None
    """Which patchset of the change it is; ``None`` when Gerrit never had it."""
    above: tuple[Commit, ...]
    """The branch's commits above it, oldest first."""


@dataclass(frozen=True)
class CheckoutPlan:
    """What is there before ``ger checkout`` acts: the patchset, the branch it goes on, its local version."""

    patchset: GerritPatchset
    branch: str
    local: LocalVersion | None
    """The local version on :attr:`branch`, if that branch has one."""
    existing_tip: str | None
    """The tip of :attr:`branch` when it exists without a local version of the change."""
    elsewhere: tuple[LocalVersion, ...]
    """Local versions on other branches: ``-b`` put the patchset somewhere else."""
    current_branch: str | None

    @property
    def state(self) -> LocalState:
        """How the local version on :attr:`branch` relates to the patchset."""
        if self.local is None:
            return LocalState.NONE
        if self.local.commit.sha == self.patchset.commit:
            return LocalState.SAME
        return LocalState.PUSHED if self.local.patch_set is not None else LocalState.EDITED


@dataclass(frozen=True)
class CheckoutResult:
    """What ``ger checkout`` did."""

    action: CheckoutAction
    branch: str
    upstream: str | None
    """The branch's upstream afterwards."""
    unchanged: bool = False
    """Already on the branch, at the patchset: nothing was done."""
    rebased: int = 0
    """Commits replayed onto the patchset above it."""
    moved_from: str | None = None
    """The old tip, when ``--force`` moved a branch off commits Gerrit does not have."""


def _gerrit_branch(cwd: Path | str, branch: str, settings: Settings) -> str | None:
    """The Gerrit branch *branch* pushes to (``main``), or ``None`` when it has no Gerrit destination."""
    destination = effective_gerrit_destination_branch(cwd, branch, settings=settings)
    return refs_for_push_branch_name(destination, settings=settings) if destination else None


def find_local_versions(cwd: Path | str, change: ResolvedChange, *, settings: Settings) -> list[LocalVersion]:
    """Every local version of *change*: a commit with its Change-Id on a local branch, above the Gerrit remote.

    A branch that pushes to another Gerrit branch holds a different change with the same
    Change-Id (a cherry-pick), not a version of this one, and is skipped.
    """
    view = change.view
    rows = read_commit_rows(
        cwd, "-i", f"--grep=^Change-Id: {view.change_id}", "--branches", "--not", f"--remotes={settings.gerrit_remote}"
    )
    versions: list[LocalVersion] = []
    for row in rows:
        if row.change_id is None or norm_change_id(row.change_id) != norm_change_id(view.change_id):
            continue
        holders = git_out("for-each-ref", "--format=%(refname:short)", f"--contains={row.sha}", "refs/heads/", cwd=cwd)
        for branch in holders.splitlines():
            if _gerrit_branch(cwd, branch, settings) not in (None, view.branch):
                continue
            above = commits_in_range(cwd, f"{row.sha}..refs/heads/{branch}", first_parent=True)
            versions.append(LocalVersion(branch, row, change.patch_set_of(row.sha), tuple(above)))
    return versions


def _pick(versions: list[LocalVersion], current: str | None, patchset: GerritPatchset) -> LocalVersion | None:
    """The one local version to act on: the only one, else the current branch's, else the identical one."""
    if len(versions) <= 1:
        return versions[0] if versions else None
    for wanted in (lambda v: v.branch == current, lambda v: v.commit.sha == patchset.commit):
        hits = [version for version in versions if wanted(version)]
        if len(hits) == 1:
            return hits[0]
    names = ", ".join(f"{v.branch} ({v.commit.short_sha})" for v in versions)
    raise CheckoutRefusal(
        f"change {patchset.number} has several local versions: {names}. Name the branch to use with -b BRANCH, "
        f"or check patchset {patchset.patch_set} out on a new branch with -b NAME"
    )


def plan_checkout(
    cwd: Path | str, change: ResolvedChange, patch_set: int | None, *, settings: Settings, branch: str | None = None
) -> CheckoutPlan:
    """What checking patchset *patch_set* (default: current) of *change* out would meet. Reads only.

    *branch* is ``-b``: the branch to put the patchset on, and the one whose local version
    counts. Without it, the change's local version decides, else ``change-<number>``.
    """
    patchset = change.patchset(patch_set)
    versions = find_local_versions(cwd, change, settings=settings)
    current = checked_out_branch_name(cwd)
    if branch is not None:
        local = _pick([v for v in versions if v.branch == branch], current, patchset)
    else:
        local = _pick(versions, current, patchset)
    name = branch or (local.branch if local else f"{BRANCH_PREFIX}{patchset.number}")
    return CheckoutPlan(
        patchset=patchset,
        branch=name,
        local=local,
        existing_tip=None if local else commit_sha_or_none(cwd, f"refs/heads/{name}"),
        elsewhere=tuple(v for v in versions if v.branch != name),
        current_branch=current,
    )


def choose_action(plan: CheckoutPlan, *, update: bool | None, force: bool) -> CheckoutAction | None:
    """What to do given ``--update`` / ``--no-update`` (*update*) and ``--force``; ``None``: ask the user.

    Only the update of a pushed local version is a question; a local version with edits of
    yours is never replaced without ``--force``.
    """
    patchset = plan.patchset
    state = plan.state
    if state is LocalState.NONE:
        if plan.existing_tip is not None and not force:
            raise CheckoutRefusal(
                f"branch {plan.branch} already exists and does not hold change {patchset.number}: "
                f"pick another name with -b NAME, or pass --force to reset it to patchset {patchset.patch_set}"
            )
        return CheckoutAction.CREATE
    if state is LocalState.SAME or update is False:
        return CheckoutAction.SWITCH
    if state is LocalState.PUSHED:
        return CheckoutAction.UPDATE if update or force else None
    if force:
        return CheckoutAction.UPDATE
    local = plan.local
    assert local is not None
    raise CheckoutRefusal(
        f'{plan.branch} holds a version of change {patchset.number} that Gerrit never had ({local.commit.short_sha} "'
        f'{local.commit.subject}"), so ger will not overwrite it. Switch to it as it is with --no-update, check '
        f"patchset {patchset.patch_set} out on another branch with -b NAME, or replace your version with --force"
    )


def _refusal_to_rebase(plan: CheckoutPlan, why: str) -> CheckoutRefusal:
    local, patchset = plan.local, plan.patchset
    assert local is not None
    return CheckoutRefusal(
        f"your {len(local.above)} commit(s) above change {patchset.number} on {plan.branch} do not apply onto "
        f"patchset {patchset.patch_set} ({why}); nothing was changed. Rebase them yourself "
        f"(git rebase --onto {patchset.commit[:12]} {local.commit.short_sha} {plan.branch}), "
        f"or check patchset {patchset.patch_set} out on a branch of its own with -b NAME"
    )


def _update(cwd: Path | str, plan: CheckoutPlan) -> int:
    """Move the local version to the patchset, replaying the commits above it; how many were replayed."""
    local, target = plan.local, plan.patchset.commit
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
    """Do *action*. Refuses before touching anything while the working tree has changes to tracked files.

    The patchset is fetched with its target branch, so a branch created for it tracks an
    up-to-date ``<remote>/<target>``. A local version without a Gerrit destination gets that
    upstream too; one that has a destination keeps it.
    """
    remote, patchset, branch = settings.gerrit_remote, plan.patchset, plan.branch
    if action is CheckoutAction.SWITCH and branch == plan.current_branch:
        return CheckoutResult(action, branch, resolve_upstream_abbrev_ref(cwd, branch), unchanged=True)
    edits = own_edits(cwd, None)
    if edits:
        raise CheckoutRefusal(
            f"your working tree has changes to tracked files ({', '.join(edits)}): "
            "commit or stash them (git stash), then run ger checkout again"
        )
    rebased, moved_from = 0, None
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
    if action is CheckoutAction.CREATE or _gerrit_branch(cwd, branch, settings) is None:
        track_remote_branch(cwd, branch, remote, patchset.target_branch)
    upstream = resolve_upstream_abbrev_ref(cwd, branch)
    return CheckoutResult(action, branch, upstream, rebased=rebased, moved_from=moved_from)
