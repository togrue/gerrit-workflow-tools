"""Read endpoints of ``ger serve`` about moving the local stack: which commits can swap, and its upstream.

Spec: docu/spec/commands/serve.md.
"""

from __future__ import annotations

from itertools import pairwise
from typing import Any

from gerrit_workflow_tools.core.git_run import commit_sha_or_none
from gerrit_workflow_tools.core.reorder_preview import PreviewUnavailable, swap_conflicts
from gerrit_workflow_tools.core.stack import get_stack_snapshot, pinned_count, rewrite_base
from gerrit_workflow_tools.core.upstream import upstream_choice
from gerrit_workflow_tools.server.request import Context, Request


def stack_moves(ctx: Context, _req: Request) -> dict[str, Any]:
    """Which adjacent commits of the local stack can swap places: a conflict preview, nothing is touched.

    One entry per adjacent pair above the newest merge, oldest first: that merge and what is below
    it stay where they are. ``available`` is false when this git cannot tell.
    """
    snapshot = get_stack_snapshot(ctx.cwd, settings=ctx.settings)
    pinned = pinned_count(ctx.cwd, snapshot.commits)
    commits = snapshot.commits[pinned:]
    base = rewrite_base(ctx.cwd, snapshot, pinned)
    answer: dict[str, Any] = {"head": commit_sha_or_none(ctx.cwd, "HEAD"), "available": True, "swaps": []}
    try:
        conflicts = swap_conflicts(ctx.cwd, base, commits)
    except PreviewUnavailable as error:
        return {**answer, "available": False, "reason": str(error)}
    for (lower, upper), conflict in zip(pairwise(commits), conflicts, strict=True):
        found = conflict and {
            "sha": conflict.commit.sha,
            "summary": conflict.commit.subject,
            "files": list(conflict.files),
        }
        answer["swaps"].append({"lower": lower.sha, "upper": upper.sha, "conflict": found})
    return answer


def stack_upstream(ctx: Context, _req: Request) -> dict[str, Any]:
    """The working branch's upstream, and which to offer when it has none: the best guess first."""
    branch = ctx.run.worktree.branch
    if branch is None:
        return {"branch": None, "upstream": None, "gone": None, "suggested": None, "why": None, "candidates": []}
    choice = upstream_choice(ctx.cwd, branch, settings=ctx.settings)
    return {
        "branch": branch,
        "upstream": choice.current,
        "gone": choice.gone,
        "suggested": choice.suggested,
        "why": choice.why,
        "candidates": choice.candidates,
    }
