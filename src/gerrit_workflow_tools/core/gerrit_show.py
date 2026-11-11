"""Core resolution helpers for `ger show`."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from gerrit_workflow_tools.core.annotated_stack import annotate
from gerrit_workflow_tools.core.changeish import unresolved_change_message, unresolved_rev_message
from gerrit_workflow_tools.core.ci_content import CiContent
from gerrit_workflow_tools.core.ci_content_service import CiContentService
from gerrit_workflow_tools.core.comment_chains import CommentSelection, merge_chains
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.change_resolution import (
    ChangeResolutionError,
    Resolution,
    resolve_changeish,
)
from gerrit_workflow_tools.core.gerrit.change_view import ChangeView
from gerrit_workflow_tools.core.gerrit.rest import GerritRest, GerritUnavailableError, norm_change_id
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.gerrit_change_status import CommentChain, LogCommit, PatchsetStatus
from gerrit_workflow_tools.core.git_run import GitError, commit_sha_or_none
from gerrit_workflow_tools.core.local_comments import local_chains_of
from gerrit_workflow_tools.core.stack import Commit, commits_in_range, get_stack_snapshot, read_commit_rows


@dataclass(frozen=True)
class ShowCommitResolution:
    """Resolved commit row for status lookup plus resolution metadata."""

    row: Commit
    is_local_commit: bool
    resolution: Resolution


def gerrit_rest_key(commit: LogCommit, resolution: object, service: GerritService) -> str | None:
    """Unambiguous REST key for *commit*: selected triplet, else push-branch triplet from Change-Id.

    Bare Change-Ids 404 on follow-up endpoints when the same id exists on multiple branches
    (Gerrit: "Multiple changes found"). Stack paths (``--stack``, git ranges) often have no
    ``resolution.selected``; narrow through :meth:`GerritService.cache_key` like reply does.
    """
    selected = getattr(resolution, "selected", None)
    if selected is not None:
        return selected.triplet
    if not commit.change_id:
        return None
    return service.cache_key(commit.change_id)


def _row_from_gerrit_change(change: ChangeView) -> Commit:
    """A **commit row** for a change with no local commit: Gerrit's current revision."""
    sha = change.current_revision or ""
    change_id = change.change_id
    if not change_id:
        raise ChangeResolutionError("Gerrit change has no change_id")
    summary = change.subject
    short = sha[:8] if len(sha) >= 8 else "?" * min(8, max(1, len(sha) or 1))
    if not sha:
        short = "????????"
    return Commit(sha=sha, short_sha=short, subject=summary, body="", change_id=change_id)


def _row_from_local_commit(cwd: Path | str, sha: str) -> Commit:
    return read_commit_rows(cwd, "-1", sha)[0]


def _stack_commit_for(cwd: Path | str, change_id: str, *, branch: str | None, settings: Settings) -> Commit | None:
    """The **local stack** commit carrying *change_id*, so a change renders like its commit."""
    try:
        snap = get_stack_snapshot(cwd, branch, settings=settings)
    except GitError:
        return None
    want = norm_change_id(change_id)
    return next((c for c in snap.commits if c.valid_change_id and norm_change_id(c.valid_change_id) == want), None)


def resolve_show_commit_row(
    cwd: Path | str,
    arg: str | None,
    client: GerritRest,
    *,
    settings: Settings,
    branch: str | None = None,
) -> ShowCommitResolution:
    """Resolve one `ger show` argument via the shared changeish core.

    *arg* names a single revision (not a ``A..B`` range).
    """

    raw_arg = (arg or "HEAD").strip()
    resolution = resolve_changeish(
        raw_arg, client=client, cwd=cwd, settings=settings, branch=branch, explicit_target=True
    )

    if resolution.kind == "git-rev":
        sha = resolution.local_sha
        if sha is None:
            raise ChangeResolutionError(unresolved_rev_message(raw_arg))
        row = _row_from_local_commit(cwd, sha)
        return ShowCommitResolution(row=row, is_local_commit=True, resolution=resolution)

    if resolution.selected is None:
        raise ChangeResolutionError(unresolved_change_message(raw_arg))

    local = _stack_commit_for(cwd, resolution.selected.change_id, branch=branch, settings=settings)
    if local is not None:
        return ShowCommitResolution(
            row=local, is_local_commit=True, resolution=replace(resolution, local_sha=local.sha)
        )

    change = ChangeView.from_payload(client.get_change(resolution.selected.triplet))
    row = _row_from_gerrit_change(change)
    return ShowCommitResolution(row=row, is_local_commit=False, resolution=resolution)


def resolve_range_endpoint_sha(
    cwd: Path | str,
    arg: str,
    client: GerritRest,
    *,
    settings: Settings,
    branch: str | None = None,
) -> str:
    """Resolve a range endpoint changeish to a local commit SHA."""
    raw = arg.strip()
    if not raw:
        raise ChangeResolutionError("empty range endpoint")
    resolution = resolve_changeish(raw, client=client, cwd=cwd, settings=settings, branch=branch, explicit_target=True)
    if resolution.local_sha:
        return resolution.local_sha

    change_id: str | None = None
    if resolution.selected is not None:
        change_id = resolution.selected.change_id
    elif resolution.kind == "change-id":
        from gerrit_workflow_tools.core.changeish import parse

        parsed = parse(raw)
        change_id = parsed.change_id

    if change_id:
        want = norm_change_id(change_id)
        snap = get_stack_snapshot(cwd, branch, settings=settings)
        for commit in snap.commits:
            footer = commit.valid_change_id
            if footer and norm_change_id(footer) == want:
                return commit.sha
        if resolution.selected is not None:
            rev = ChangeView.from_payload(client.get_change(resolution.selected.triplet)).current_revision
            if rev is not None:
                local = commit_sha_or_none(cwd, rev)
                if local is not None:
                    return local

    raise ChangeResolutionError(f"range endpoint {raw!r} does not resolve to a local commit")


def parse_show_range(arg: str) -> tuple[str, str, str] | None:
    """If *arg* is ``A..B`` or ``A...B``, return ``(left, dots, right)``; else ``None``.

    Empty right defaults to ``HEAD``. Empty left is an error.
    """
    s = arg.strip()
    if "..." in s:
        left, right = s.split("...", 1)
        dots = "..."
    elif ".." in s:
        left, right = s.split("..", 1)
        dots = ".."
    else:
        return None
    left, right = left.strip(), right.strip()
    if not left:
        raise ChangeResolutionError(f"invalid revision range: {arg!r}")
    if not right:
        right = "HEAD"
    return left, dots, right


def _local_resolutions(rows: Iterable[Commit], *, input_arg: str) -> list[ShowCommitResolution]:
    return [
        ShowCommitResolution(
            row=row,
            is_local_commit=True,
            resolution=Resolution(input=input_arg, kind="git-rev", local_sha=row.sha),
        )
        for row in rows
    ]


def expand_show_range(
    cwd: Path | str,
    arg: str,
    client: GerritRest,
    *,
    settings: Settings,
    branch: str | None = None,
) -> list[ShowCommitResolution]:
    """Expand a changeish-resolved ``A..B`` / ``A...B`` range to local commits (oldest first)."""
    parsed = parse_show_range(arg)
    if parsed is None:
        raise ChangeResolutionError(f"not a revision range: {arg!r}")
    left, dots, right = parsed
    left_sha = resolve_range_endpoint_sha(cwd, left, client, settings=settings, branch=branch)
    right_sha = resolve_range_endpoint_sha(cwd, right, client, settings=settings, branch=branch)
    rev_range = f"{left_sha}{dots}{right_sha}"
    return _local_resolutions(commits_in_range(cwd, rev_range), input_arg=arg)


def expand_stack_range(
    cwd: Path | str,
    *,
    branch: str | None = None,
    settings: Settings | None = None,
) -> list[ShowCommitResolution]:
    """Oldest-first commits in ``upstream_tip..HEAD``."""
    snap = get_stack_snapshot(cwd, branch, settings=settings)
    return _local_resolutions(snap.commits, input_arg="--stack")


def _dedupe_key(resolved: ShowCommitResolution) -> str:
    cid = resolved.row.valid_change_id
    if cid:
        return f"cid:{norm_change_id(cid)}"
    sha = resolved.row.sha
    if sha:
        return f"sha:{sha}"
    return f"id:{id(resolved)}"


def resolve_show_targets(
    cwd: Path | str,
    args: list[str],
    client: GerritRest,
    *,
    settings: Settings,
    stack: bool = False,
    branch: str | None = None,
) -> list[ShowCommitResolution]:
    """Resolve positional changeishes / ranges and optional ``--stack`` into a deduped list.

    With no *args* and ``stack=False``, resolves ``HEAD``. Order: ``--stack`` commits first
    (oldest-first), then each positional arg in order (ranges oldest-first). Duplicates
    (same Change-Id, else same SHA) keep the first occurrence.
    """
    pieces: list[ShowCommitResolution] = []
    if stack:
        pieces.extend(expand_stack_range(cwd, branch=branch, settings=settings))
    if not args and not stack:
        pieces.append(resolve_show_commit_row(cwd, "HEAD", client, settings=settings, branch=branch))
    for raw in args:
        if parse_show_range(raw) is not None:
            pieces.extend(expand_show_range(cwd, raw, client, settings=settings, branch=branch))
        else:
            pieces.append(resolve_show_commit_row(cwd, raw, client, settings=settings, branch=branch))

    first_by_key: dict[str, ShowCommitResolution] = {}
    for item in pieces:
        first_by_key.setdefault(_dedupe_key(item), item)
    return list(first_by_key.values())


@dataclass(frozen=True)
class ShownCommit:
    """One resolved target with everything ``ger show`` reports about it."""

    target: ShowCommitResolution
    commit: LogCommit
    chains: list[CommentChain]
    """Gerrit and local comment chains, as selected."""
    ci_contents: list[CiContent] | None
    """**CI content** per failed **CI link**; ``None`` when it was not asked for."""
    gerrit_unavailable: GerritUnavailableError | None = None
    """Why Gerrit could not be asked; :attr:`commit` then holds local state only."""

    @property
    def ci_verdict_contents(self) -> list[CiContent] | None:
        """:attr:`ci_contents` when Gerrit's CI verdict is about this very commit, else ``None``.

        A commit that is not on Gerrit, or is ahead of the patchset CI ran on, has no known CI state.
        """
        if not self.commit.pushed or self.commit.patchset_status == PatchsetStatus.NEWER:
            return None
        return self.ci_contents


def load_shown_commits(
    targets: list[ShowCommitResolution],
    *,
    service: GerritService,
    comments: CommentSelection = "unresolved",
    context_padding: int | None = None,
    fetch_ci_pipelines: bool = False,
    ci: CiContentService | None = None,
) -> list[ShownCommit]:
    """Overlay, attention, comment chains and (with *ci*) **CI content** for *targets*, in order.

    *targets* must be in stack order for chain-blocking attention. A CI link that cannot be
    fetched stays on its result; nothing here raises for it.
    """
    annotated = annotate(
        [t.row for t in targets], service=service, cwd=service.cwd, fetch_ci_pipelines=fetch_ci_pipelines
    )
    commits = annotated.commits
    if len(commits) != len(targets):
        raise ChangeResolutionError("commit annotation mismatch")
    shown: list[ShownCommit] = []
    for target, commit in zip(targets, commits, strict=True):
        ci_contents = ci.fetch(commit.ci_links) if ci is not None else None
        rest_key = gerrit_rest_key(commit, target.resolution, service)
        chains: list[CommentChain] = []
        if commit.pushed and rest_key:
            chains = service.comments.get_chains(
                rest_key, change_updated=commit.freshness, context_padding=context_padding, comments=comments
            )
        if rest_key:
            chains = merge_chains(chains, local_chains_of(service, rest_key, commit.sha or None, comments=comments))
        shown.append(ShownCommit(target, commit, chains, ci_contents, annotated.gerrit_unavailable))
    return shown
