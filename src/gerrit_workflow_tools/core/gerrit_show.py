"""Core resolution helpers for `ger show`."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gerrit_workflow_tools.core.change_id import extract_valid_change_id
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.change_resolution import (
    ChangeResolutionError,
    Resolution,
    resolve_changeish,
)
from gerrit_workflow_tools.core.gerrit.rest import GerritRest, norm_change_id
from gerrit_workflow_tools.core.gerrit_change_status import CommitStatusInput
from gerrit_workflow_tools.core.git_run import git, git_out
from gerrit_workflow_tools.core.stack import Commit, commits_in_range, get_stack_snapshot


@dataclass(frozen=True)
class ShowCommitResolution:
    """Resolved commit row for status lookup plus resolution metadata."""

    row: CommitStatusInput
    is_local_commit: bool
    resolution: Resolution


def _row_from_gerrit_change(change: dict[str, object]) -> CommitStatusInput:
    rev = change.get("current_revision")
    sha = rev if isinstance(rev, str) else ""
    change_id = change.get("change_id")
    if not isinstance(change_id, str):
        raise ChangeResolutionError("Gerrit change has no change_id")
    subject = change.get("subject")
    summary = subject if isinstance(subject, str) else ""
    short = sha[:8] if len(sha) >= 8 else "?" * min(8, max(1, len(sha) or 1))
    if not sha:
        short = "????????"
    return CommitStatusInput(sha=sha, short_sha=short, summary=summary, change_id=change_id)


def _row_from_local_commit(cwd: Path | str, sha: str) -> CommitStatusInput:
    raw = git_out("log", "-1", "--format=%B", sha, cwd=cwd)
    summary = git_out("log", "-1", "--format=%s", sha, cwd=cwd)
    short = git_out("log", "-1", "--format=%h", sha, cwd=cwd)
    change_id = extract_valid_change_id(raw)
    return CommitStatusInput(sha=sha, short_sha=short, summary=summary, change_id=change_id)


def _row_from_stack_commit(commit: Commit) -> CommitStatusInput:
    return CommitStatusInput(
        sha=commit.sha,
        short_sha=commit.short_sha,
        summary=commit.subject,
        change_id=extract_valid_change_id(commit.body),
    )


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

    change = client.get_change(resolution.selected.triplet)
    row = _row_from_gerrit_change(change)
    return ShowCommitResolution(row=row, is_local_commit=False, resolution=resolution)


def _local_sha_or_none(cwd: Path | str, rev: str) -> str | None:
    p = git("rev-parse", "-q", "--verify", f"{rev}^{{commit}}", cwd=cwd, check=False)
    if p.returncode != 0:
        return None
    return p.stdout.strip() or None


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
            footer = extract_valid_change_id(commit.body)
            if footer and norm_change_id(footer) == want:
                return commit.sha
        if resolution.selected is not None:
            change = client.get_change(resolution.selected.triplet)
            rev = change.get("current_revision")
            if isinstance(rev, str):
                local = _local_sha_or_none(cwd, rev)
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


def _resolution_for_local_sha(sha: str, *, input_arg: str) -> Resolution:
    return Resolution(input=input_arg, kind="git-rev", local_sha=sha)


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
    rows = commits_in_range(cwd, rev_range)
    return [
        ShowCommitResolution(
            row=_row_from_stack_commit(c),
            is_local_commit=True,
            resolution=_resolution_for_local_sha(c.sha, input_arg=arg),
        )
        for c in rows
    ]


def expand_stack_range(
    cwd: Path | str,
    *,
    branch: str | None = None,
    settings: Settings | None = None,
) -> list[ShowCommitResolution]:
    """Oldest-first commits in ``upstream_tip..HEAD``."""
    snap = get_stack_snapshot(cwd, branch, settings=settings)
    return [
        ShowCommitResolution(
            row=_row_from_stack_commit(c),
            is_local_commit=True,
            resolution=_resolution_for_local_sha(c.sha, input_arg="--stack"),
        )
        for c in snap.commits
    ]


def _dedupe_key(resolved: ShowCommitResolution) -> str:
    cid = resolved.row.change_id
    if isinstance(cid, str) and cid:
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

    seen: set[str] = set()
    out: list[ShowCommitResolution] = []
    for item in pieces:
        key = _dedupe_key(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out
