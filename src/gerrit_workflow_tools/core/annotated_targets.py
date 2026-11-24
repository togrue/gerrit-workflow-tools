"""Commits named on the command line (``ger show``, ``ger ci``), annotated as the local stack sees them.

A commit of the **local stack** is judged with the commits below it, so ``ger show HEAD`` says
what ``ger log`` says of HEAD: chain-blocking included. A commit the upstream already contains
counts as merged, as in ``ger log`` with a range. Anything else (a change named by number, a
commit on another branch) is judged on its own.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from gerrit_workflow_tools.core.annotated_stack import AnnotatedStack, annotate
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.git_run import GitError, git
from gerrit_workflow_tools.core.stack import Commit, StackSnapshot, get_stack_snapshot
from gerrit_workflow_tools.core.upstream import NeedsUpstreamError


def local_stack_or_none(cwd: Path | str, branch: str | None, *, settings: Settings) -> StackSnapshot | None:
    """*branch*'s local stack, or ``None`` without a branch or an upstream to take it from."""
    if branch is None:
        return None
    try:
        return get_stack_snapshot(cwd, branch, first_parent=True, settings=settings)
    except (GitError, NeedsUpstreamError):
        return None


def _upstream_contains(cwd: Path | str, shas: Sequence[str], upstream_tip: str) -> frozenset[str]:
    return frozenset(
        sha
        for sha in shas
        if git("merge-base", "--is-ancestor", sha, upstream_tip, cwd=cwd, check=False).returncode == 0
    )


def annotate_targets(
    rows: list[Commit],
    *,
    service: GerritService,
    stack: StackSnapshot | None,
    local: Sequence[bool],
    triplets: Sequence[str | None] | None = None,
    fetch_ci_pipelines: bool = False,
) -> AnnotatedStack:
    """Overlay and attention for *rows*, in order; each row of *stack* judged within the whole stack.

    *local* says, per row, whether it is a local commit (not a change's revision from Gerrit);
    of those outside the stack, the ones the upstream contains count as merged. The result's
    :attr:`AnnotatedStack.commits` match *rows*; its blocker may be a commit below them.
    """
    stack_rows = list(stack.commits) if stack else []
    position = {c.sha: i for i, c in enumerate(stack_rows)}
    # The whole stack, not only up to the highest row: whether a row blocks the chain depends on those above.
    context = stack_rows if any(r.sha in position for r in rows) else []
    others = [i for i, r in enumerate(rows) if r.sha not in position]
    outside = [rows[i].sha for i in others if local[i] and rows[i].sha]
    on_target = _upstream_contains(service.cwd, outside, stack.upstream_tip) if stack and outside else frozenset()
    shown_triplets = list(triplets) if triplets is not None else [None] * len(rows)

    def overlay(part: list[Commit], part_triplets: list[str | None], on: frozenset[str]) -> AnnotatedStack:
        return annotate(
            part,
            service=service,
            cwd=service.cwd,
            fetch_ci_pipelines=fetch_ci_pipelines,
            on_target=on,
            triplets=part_triplets,
        )

    # The stack and the rest are two chains: a change of somebody else's is not blocked by yours.
    judged = overlay(context, [None] * len(context), frozenset()) if context else AnnotatedStack()
    rest = overlay([rows[i] for i in others], [shown_triplets[i] for i in others], on_target) if others else None
    after = dict(zip(others, rest.commits, strict=True)) if rest else {}
    commits = [judged.commits[position[r.sha]] if r.sha in position else after[i] for i, r in enumerate(rows)]
    unavailable = judged.gerrit_unavailable or (rest.gerrit_unavailable if rest else None)
    blocker = judged.blocker or (rest.blocker if rest else None)
    return AnnotatedStack(
        commits=commits, rows=list(rows), gerrit_unavailable=unavailable, blocker=blocker, around=judged.commits
    )
