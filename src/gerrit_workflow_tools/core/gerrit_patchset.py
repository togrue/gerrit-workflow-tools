"""One Gerrit patchset: which one a changeish names, and fetching it from ``gerrit.remote``.

Every command that brings a change's commit into the repository starts here: ``ger review``
checks it out to review, ``ger serve`` diffs it, the **review delta** compares two of them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gerrit_workflow_tools.core.changeish import parse as parse_changeish
from gerrit_workflow_tools.core.gerrit.change_resolution import Resolution, resolve_changeish
from gerrit_workflow_tools.core.gerrit.change_view import ChangeView
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.gerrit.stack_context import ChangeResolutionError, build_triplet
from gerrit_workflow_tools.core.git_run import GitError, commit_sha_or_none, git
from gerrit_workflow_tools.core.git_state import resolve_working_branch


@dataclass(frozen=True)
class GerritPatchset:
    """One patchset of a change, as Gerrit describes it."""

    number: int
    key: str
    """The change's triplet."""
    change_id: str
    patch_set: int
    commit: str
    subject: str
    target_branch: str


@dataclass(frozen=True)
class ResolvedChange:
    """The Gerrit change a changeish named, and how resolution got there."""

    view: ChangeView
    key: str
    """The change's triplet."""
    resolution: Resolution
    wanted: int | None
    """The patchset the changeish itself names (a ``refs/changes/…`` ref does), else ``None``."""

    def patchset(self, patch_set: int | None = None) -> GerritPatchset:
        """Patchset *patch_set*, else the one the changeish names, else the current one."""
        view = self.view
        wanted = patch_set or self.wanted or view.current_revision_number
        sha = next((sha for sha, rev in view.revisions.items() if rev.number == wanted), None)
        if view.number is None or wanted is None or sha is None:
            raise ChangeResolutionError(f"change {view.number} has no patchset {wanted}")
        return GerritPatchset(
            number=view.number,
            key=self.key,
            change_id=view.change_id,
            patch_set=wanted,
            commit=sha,
            subject=view.subject,
            target_branch=view.branch,
        )


def changes_ref(number: int, patch_set: int) -> str:
    """Gerrit's ``refs/changes/NN/<number>/<patchset>`` ref."""
    return f"refs/changes/{number % 100:02d}/{number}/{patch_set}"


def resolve_change(service: GerritService, cwd: Path | str, changeish: str) -> ResolvedChange:
    """The one Gerrit change *changeish* names, read once.

    A bare number is a change number here: nobody checks a commit out by an all-digit SHA.
    """
    if changeish.isdigit():
        changeish = f"change:{changeish}"
    branch = resolve_working_branch(cwd, settings=service.settings)
    resolution = resolve_changeish(
        changeish, client=service.rest, cwd=cwd, settings=service.settings, branch=branch, explicit_target=True
    )
    if resolution.selected is None:
        raise ChangeResolutionError(f"{changeish!r} names no Gerrit change")
    view = ChangeView.from_payload(service.rest.get_change(resolution.selected.triplet))
    # A triplet, never Gerrit's `id`: real servers send the compact project~number form.
    key = build_triplet(view.project, view.branch, view.change_id) if view.project else resolution.selected.triplet
    return ResolvedChange(view=view, key=key, resolution=resolution, wanted=parse_changeish(changeish).patchset)


def resolve_patchset(
    service: GerritService, cwd: Path | str, changeish: str, patch_set: int | None
) -> tuple[GerritPatchset, str | None]:
    """The Gerrit patchset *changeish* names (*patch_set* or the URL's, else current), and its URL."""
    change = resolve_change(service, cwd, changeish)
    return change.patchset(patch_set), change.view.url(service.web_base)


def fetch_patchset(cwd: Path | str, remote: str, patchset: GerritPatchset) -> None:
    """Fetch *patchset* from *remote* by its ``refs/changes/…`` ref; it must bring the patchset's commit."""
    ref = changes_ref(patchset.number, patchset.patch_set)
    git("fetch", "-q", remote, ref, cwd=cwd)
    if commit_sha_or_none(cwd, patchset.commit) is None:
        raise GitError(f"fetching {ref} did not bring {patchset.commit[:12]}")
