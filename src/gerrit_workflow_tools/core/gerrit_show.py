"""Core resolution helpers for `ger show`."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.change_resolution import (
    ChangeResolutionError,
    Resolution,
    resolve_changeish,
)
from gerrit_workflow_tools.core.gerrit.change_view import ChangeView
from gerrit_workflow_tools.core.gerrit.rest import GerritRest, norm_change_id
from gerrit_workflow_tools.core.git_run import GitError, commit_sha_or_none
from gerrit_workflow_tools.core.stack import Commit, commits_in_range, get_stack_snapshot, read_commit_rows


@dataclass(frozen=True)
class ShowCommitResolution:
    """Resolved commit row for status lookup plus resolution metadata."""

    row: Commit
    is_local_commit: bool
    resolution: Resolution


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
            raise ChangeResolutionError(f"cannot resolve git revision {raw_arg!r}")
        row = _row_from_local_commit(cwd, sha)
        return ShowCommitResolution(row=row, is_local_commit=True, resolution=resolution)

    if resolution.selected is None:
        raise ChangeResolutionError(f"no matching Gerrit change for {raw_arg!r}")

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
