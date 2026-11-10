"""Which Gerrit patchset a review checks out, and the review chain it walks.

The Gerrit half of ``ger review``: :mod:`~gerrit_workflow_tools.core.review_checkout` only
runs git, so everything that asks Gerrit what to check out lives here.
"""

from __future__ import annotations

from pathlib import Path

from gerrit_workflow_tools.core.changeish import parse as parse_changeish
from gerrit_workflow_tools.core.gerrit.change_resolution import resolve_changeish
from gerrit_workflow_tools.core.gerrit.change_view import ChangeView
from gerrit_workflow_tools.core.gerrit.rest import GerritApiError
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.gerrit.stack_context import ChangeResolutionError, build_triplet
from gerrit_workflow_tools.core.git_state import resolve_working_branch
from gerrit_workflow_tools.core.review_checkout import PatchsetToReview, chain_numbers


def patchset_to_review(
    service: GerritService, cwd: Path | str, changeish: str, patch_set: int | None
) -> tuple[PatchsetToReview, str | None]:
    """The Gerrit patchset *changeish* names (*patch_set* or the URL's, else current), and its URL.

    A bare number is a change number here: nobody reviews a commit by an all-digit SHA.
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
    wanted = patch_set or parse_changeish(changeish).patchset or view.current_revision_number
    sha = next((sha for sha, rev in view.revisions.items() if rev.number == wanted), None)
    if view.number is None or wanted is None or sha is None:
        raise ChangeResolutionError(f"change {view.number} has no patchset {wanted}")
    target = PatchsetToReview(
        number=view.number,
        # A triplet, never Gerrit's `id`: real servers send the compact project~number form.
        key=build_triplet(view.project, view.branch, view.change_id) if view.project else resolution.selected.triplet,
        change_id=view.change_id,
        patch_set=wanted,
        commit=sha,
        subject=view.subject,
        target_branch=view.branch,
    )
    return target, view.url(service.web_base)


def related_chain_numbers(service: GerritService, key: str, patch_set: int | str = "current") -> list[int]:
    """Open change numbers of the review chain *key* sits in, base first; ``[]`` when Gerrit cannot say."""
    try:
        return chain_numbers(service.rest.get_related(key, patch_set))
    except GerritApiError:
        return []
