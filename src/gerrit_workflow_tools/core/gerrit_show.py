"""Core resolution helpers for `ger show`."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gerrit_workflow_tools.core.gerrit.change_resolution import (
    ChangeResolutionError,
    Resolution,
    resolve_changeish,
)
from gerrit_workflow_tools.core.gerrit.rest import GerritClient
from gerrit_workflow_tools.core.gerrit_change_status import CommitStatusInput
from gerrit_workflow_tools.core.git_run import GitError, git_out
from gerrit_workflow_tools.core.stack import parse_change_id


@dataclass(frozen=True)
class ShowCommitResolution:
    """Resolved commit row for status lookup plus resolution metadata."""

    row: CommitStatusInput
    is_local_commit: bool
    resolution: Resolution


def _arg_has_range(arg: str) -> bool:
    token = arg.strip()
    return ".." in token or "..." in token


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


def resolve_show_commit_row(cwd: Path | str, arg: str | None, client: GerritClient) -> ShowCommitResolution:
    """Resolve one `ger show` argument via the shared changeish core."""

    raw_arg = (arg or "HEAD").strip()
    if _arg_has_range(raw_arg):
        raise GitError(f"ger show does not support revision ranges: {arg!r}")

    resolution = resolve_changeish(raw_arg, client=client, cwd=cwd, explicit_target=True)

    if resolution.kind == "git-rev":
        sha = resolution.local_sha
        if sha is None:
            raise ChangeResolutionError(f"cannot resolve git revision {raw_arg!r}")
        raw = git_out("log", "-1", "--format=%B", sha, cwd=cwd)
        summary = git_out("log", "-1", "--format=%s", sha, cwd=cwd)
        short = git_out("log", "-1", "--format=%h", sha, cwd=cwd)
        change_id = parse_change_id(raw)
        row = CommitStatusInput(sha=sha, short_sha=short, summary=summary, change_id=change_id)
        return ShowCommitResolution(row=row, is_local_commit=True, resolution=resolution)

    if resolution.selected is None:
        raise ChangeResolutionError(f"no matching Gerrit change for {raw_arg!r}")

    change = client.get_change(resolution.selected.triplet)
    row = _row_from_gerrit_change(change)
    return ShowCommitResolution(row=row, is_local_commit=False, resolution=resolution)
