"""The review chain a review walks: Gerrit's related changes of the reviewed patchset.

Which patchset a review checks out is :mod:`~gerrit_workflow_tools.core.gerrit_patchset`'s;
:mod:`~gerrit_workflow_tools.core.review_checkout` only runs git.
"""

from __future__ import annotations

from gerrit_workflow_tools.core.gerrit.rest import GerritApiError
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.review_checkout import chain_numbers


def related_chain_numbers(service: GerritService, key: str, patch_set: int | str = "current") -> list[int]:
    """Open change numbers of the review chain *key* sits in, base first; ``[]`` when Gerrit cannot say."""
    try:
        return chain_numbers(service.rest.get_related(key, patch_set))
    except GerritApiError:
        return []
