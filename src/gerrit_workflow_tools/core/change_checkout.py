"""Checking a Gerrit change out to work on it: its branch, its upstream, and its **local version**.

Spec: docu/spec/commands/checkout.md. Looking, deciding and doing are apart, so the command
can ask in between: :func:`plan_checkout` only reads the repository, :func:`choose_action`
turns the user's flags into what to do (``None`` when only the user can say), and
``core/change_checkout_apply.py`` does it. Nothing here prints or prompts.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.change_view import ChangeView
from gerrit_workflow_tools.core.gerrit.rest import norm_change_id
from gerrit_workflow_tools.core.gerrit.stack_context import build_triplet
from gerrit_workflow_tools.core.gerrit_change_status import PatchsetStatus, patchset_status
from gerrit_workflow_tools.core.gerrit_patchset import GerritPatchset, ResolvedChange, patchset_of
from gerrit_workflow_tools.core.git_run import commit_sha_or_none, git_out
from gerrit_workflow_tools.core.git_state import (
    checked_out_branch_name,
    effective_gerrit_destination_branch,
    is_bookkeeping_branch,
    refs_for_push_branch_name,
)
from gerrit_workflow_tools.core.review_checkout import BRANCH_PREFIX
from gerrit_workflow_tools.core.stack import Commit, commits_in_range, read_commit_rows
from gerrit_workflow_tools.core.usage import Refusal


if TYPE_CHECKING:
    from gerrit_workflow_tools.core.gerrit.changes import ChangeApi


class CheckoutRefusal(Refusal):
    """ger will not go on without the user's decision: their commits or branches are in the way (exit 1)."""


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
    MERGE = "merge"
    """Keep both: your edits of the local version, merged into the patchset; the commits above it are
    rebased onto the result."""


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
    edit_base: tuple[int, str] | None = None
    """For a local version Gerrit never had: ``(patchset, commit)`` of the newest patchset of the change
    this repository has, the one the edits were most likely made on. What :attr:`CheckoutAction.MERGE`
    merges from."""
    gerrit_above: tuple[GerritPatchset, ...] = ()
    """The lowest commits above the local version that take Gerrit's current patchset of their change
    when the local version becomes the patchset: each is an older patchset of its change (no edits of
    yours), and Gerrit's current one sits on the one below (:func:`gerrit_above`)."""

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
    merged_from: int | None = None
    """For a merge: the patchset your edits were taken against."""
    took: tuple[GerritPatchset, ...] = ()
    """The commits above it that became Gerrit's current patchset of their change
    (:attr:`CheckoutPlan.gerrit_above`)."""
    same_as_patchset: bool = False
    """For a merge: your version held nothing the patchset does not, so the branch has the patchset itself."""


def gerrit_branch(cwd: Path | str, branch: str, settings: Settings) -> str | None:
    """The Gerrit branch *branch* pushes to (``main``), or ``None`` when it has no Gerrit destination."""
    destination = effective_gerrit_destination_branch(cwd, branch, settings=settings)
    return refs_for_push_branch_name(destination, settings=settings) if destination else None


def find_local_versions(cwd: Path | str, change: ResolvedChange, *, settings: Settings) -> list[LocalVersion]:
    """Every local version of *change*: a commit with its Change-Id on a local branch, above the Gerrit remote.

    A branch that pushes to another Gerrit branch holds a different change with the same
    Change-Id (a cherry-pick), not a version of this one, and is skipped; so are ger's own
    bookkeeping branches (``lastPush/…``).
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
            if is_bookkeeping_branch(branch) or gerrit_branch(cwd, branch, settings) not in (None, view.branch):
                continue
            above = commits_in_range(cwd, f"{row.sha}..refs/heads/{branch}", first_parent=True)
            versions.append(LocalVersion(branch, row, change.patch_set_of(row.sha), tuple(above)))
    return versions


def _pick(versions: list[LocalVersion], current: str | None, patchset: GerritPatchset) -> LocalVersion | None:
    """The one local version to act on; the first rule that leaves exactly one wins.

    The only one; else the one at a branch tip (the current branch's first), the way back to a
    change after checking out the one stacked on it; else the ``change-<number>`` branch; else the
    current branch's; else the one that is the patchset. Only when none decides is it a question.
    """
    if len(versions) <= 1:
        return versions[0] if versions else None
    named = f"{BRANCH_PREFIX}{patchset.number}"
    for wanted in (
        lambda v: not v.above and v.branch == current,
        lambda v: not v.above,
        lambda v: v.branch == named,
        lambda v: v.branch == current,
        lambda v: v.commit.sha == patchset.commit,
    ):
        hits = [version for version in versions if wanted(version)]
        if len(hits) == 1:
            return hits[0]
    names = ", ".join(f"{v.branch} ({v.commit.short_sha})" for v in versions)
    raise CheckoutRefusal(
        f"change {patchset.number} has several local versions: {names}. Name the branch to use with -b BRANCH, "
        f"or check patchset {patchset.patch_set} out on a new branch with -b NAME"
    )


def plan_checkout(
    cwd: Path | str,
    change: ResolvedChange,
    patch_set: int | None,
    *,
    settings: Settings,
    branch: str | None = None,
    changes: ChangeApi | None = None,
) -> CheckoutPlan:
    """What checking patchset *patch_set* (default: current) of *change* out would meet. Reads only.

    *branch* is ``-b``: the branch to put the patchset on, and the one whose local version
    counts. Without it, the change's local version decides, else ``change-<number>``. *changes*
    asks Gerrit about the commits above the local version (:func:`gerrit_above`); without it none
    are taken.
    """
    patchset = change.patchset(patch_set)
    versions = find_local_versions(cwd, change, settings=settings)
    current = checked_out_branch_name(cwd)
    if branch is not None:
        local = _pick([v for v in versions if v.branch == branch], current, patchset)
    else:
        local = _pick(versions, current, patchset)
    name = branch or (local.branch if local else f"{BRANCH_PREFIX}{patchset.number}")
    edited = local is not None and local.patch_set is None
    return CheckoutPlan(
        patchset=patchset,
        branch=name,
        local=local,
        existing_tip=None if local else commit_sha_or_none(cwd, f"refs/heads/{name}"),
        elsewhere=tuple(v for v in versions if v.branch != name),
        current_branch=current,
        edit_base=_edit_base(cwd, change, patchset) if edited else None,
        gerrit_above=gerrit_above(changes, change.view, patchset, local.above) if changes and local else (),
    )


def gerrit_above(
    changes: ChangeApi, view: ChangeView, patchset: GerritPatchset, above: Sequence[Commit]
) -> tuple[GerritPatchset, ...]:
    """Gerrit's current patchsets for the lowest of *above*, when the commit below them becomes *patchset*.

    One by one from the bottom, while the commit is an older patchset of its open change (so it holds
    no edits of yours) and Gerrit's current patchset of that change sits on the one below: taking it
    as Gerrit has it leaves nothing to push. The first that does not stops it; it and the rest are
    rebased as before. *view* is the change being checked out: the others are on its project and branch.
    """
    keys = {c.sha: build_triplet(view.project, view.branch, c.valid_change_id) for c in above if c.valid_change_id}
    payloads = changes.get_payloads(list(keys.values())) if keys else {}
    taken: list[GerritPatchset] = []
    below = patchset.commit
    for commit in above:
        payload = payloads.get(keys.get(commit.sha, ""))
        other = ChangeView.from_payload(payload) if payload else None
        if other is None or not other.is_open or patchset_status(commit.sha, other) is not PatchsetStatus.OUTDATED:
            break
        if other.first_parent != below:
            break
        taken.append(patchset_of(other, keys[commit.sha]))
        below = taken[-1].commit
    return tuple(taken)


def _edit_base(cwd: Path | str, change: ResolvedChange, patchset: GerritPatchset) -> tuple[int, str] | None:
    """``(patchset, commit)``: the newest patchset of *change* this repository has, other than *patchset*."""
    known = sorted(
        ((rev.number, sha) for sha, rev in change.view.revisions.items() if rev.number and sha != patchset.commit),
        reverse=True,
    )
    return next(((number, sha) for number, sha in known if commit_sha_or_none(cwd, sha)), None)


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
    if update and plan.edit_base is not None:
        return CheckoutAction.MERGE
    merge = "merge your edits into it with --update, " if plan.edit_base is not None else ""
    raise CheckoutRefusal(
        f'{plan.branch} holds a version of change {patchset.number} that Gerrit never had ({local.commit.short_sha} "'
        f'{local.commit.subject}"), so ger will not overwrite it. Switch to it as it is with --no-update, {merge}check '
        f"patchset {patchset.patch_set} out on another branch with -b NAME, or replace your version with --force"
    )
