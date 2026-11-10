"""The inbox: its **inbox sections**, their queries, and what a section adds up to.

Host-scoped (ADR-0004): nothing here resolves a stack context or looks at local commits.
"""

from __future__ import annotations

from gerrit_workflow_tools.core.attention import Attention
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.review_chain import ReviewChain


DEFAULT_TO_REVIEW_QUERY = "is:open -is:wip -is:private -owner:self reviewer:self"
DEFAULT_TO_REVIEW_ALL_QUERY = "is:open -owner:self reviewer:self"


def build_to_review_query(
    settings: Settings,
    *,
    projects: list[str],
    include_unready: bool,
) -> str:
    """Section query for *to review*, with verified gate and project filters folded in."""
    custom = settings.inbox_to_review_query
    if custom:
        base = custom
    elif include_unready:
        base = DEFAULT_TO_REVIEW_ALL_QUERY
    else:
        base = DEFAULT_TO_REVIEW_QUERY
        if settings.inbox_require_verified:
            base = f"{base} label:{settings.inbox_verified_label}+1"
    return with_project_filter(base, list(projects) if projects else list(settings.inbox_projects))


def with_project_filter(query: str, projects: list[str]) -> str:
    """*query* narrowed to *projects*; unchanged when there are none."""
    if not projects:
        return query
    if len(projects) == 1:
        return f"{query} project:{projects[0]}"
    inner = " OR ".join(f"project:{name}" for name in projects)
    return f"{query} ({inner})"


def section_summary(chains: list[ReviewChain]) -> dict[str, int]:
    """Counts over one section's chains: sizes, oldest clocks, chains with failed CI or comments."""
    return {
        "chains": len(chains),
        "changes": sum(chain.depth for chain in chains),
        "oldest_unreviewed_seconds": max((chain.unreviewed_age_seconds for chain in chains), default=0),
        "oldest_wait_seconds": max((chain.wait_age_seconds for chain in chains), default=0),
        "ci_failures": sum(1 for chain in chains if Attention.CI_FAILED in chain.attention_reasons),
        "comments": sum(1 for chain in chains if Attention.UNRESOLVED_COMMENTS in chain.attention_reasons),
    }
