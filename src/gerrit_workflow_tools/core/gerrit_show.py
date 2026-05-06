"""Core resolution helpers for `ger show`."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gerrit_workflow_tools.core.change_id import CHANGE_ID_VALUE_RE, is_change_id_token
from gerrit_workflow_tools.core.gerrit_change_status import CommitStatusInput
from gerrit_workflow_tools.core.gerrit_client import GerritClient, resolve_gerrit_change
from gerrit_workflow_tools.core.git_run import GitError, git_out
from gerrit_workflow_tools.core.stack import parse_change_id


@dataclass(frozen=True)
class ShowCommitResolution:
    """Resolved commit row for status lookup plus source marker."""

    row: CommitStatusInput
    is_local_commit: bool


def _arg_has_range(arg: str) -> bool:
    token = arg.strip()
    return ".." in token or "..." in token


def _looks_like_change_id(arg: str) -> bool:
    token = arg.strip()
    if is_change_id_token(token):
        return True
    return bool(CHANGE_ID_VALUE_RE.match(token))


def _is_numeric_change(arg: str) -> bool:
    token = arg.strip()
    return bool(token) and token.isdigit()


def _normalize_user_arg(arg: str) -> str:
    token = arg.strip()
    if not token:
        raise GitError(
            "git log failed: empty revision",
            stderr="",
            returncode=-1,
        )
    return token


def _resolved_row_from_gerrit(client: GerritClient, raw_arg: str) -> CommitStatusInput:
    change = resolve_gerrit_change(client, change_arg=raw_arg, local_change_id=None)
    rev = change.get("current_revision")
    sha = rev if isinstance(rev, str) else ""
    change_id = change.get("change_id")
    if not isinstance(change_id, str):
        raise GitError("Gerrit change has no change_id")
    subject = change.get("subject")
    summary = subject if isinstance(subject, str) else ""
    short = sha[:8] if len(sha) >= 8 else "?" * min(8, max(1, len(sha) or 1))
    if not sha:
        short = "????????"
    return CommitStatusInput(sha=sha, short_sha=short, summary=summary, change_id=change_id)


def resolve_show_commit_row(cwd: Path | str, arg: str | None, client: GerritClient) -> ShowCommitResolution:
    """Resolve one `ger show` argument to a structured status input row."""

    raw_arg = (arg or "HEAD").strip()
    if _arg_has_range(raw_arg):
        raise GitError(f"ger show does not support revision ranges: {arg!r}")

    if _looks_like_change_id(raw_arg) or _is_numeric_change(raw_arg):
        return ShowCommitResolution(row=_resolved_row_from_gerrit(client, raw_arg), is_local_commit=False)

    try:
        resolved = _normalize_user_arg(raw_arg)
    except GitError:
        return ShowCommitResolution(row=_resolved_row_from_gerrit(client, raw_arg), is_local_commit=False)

    if ".." in resolved or "..." in resolved:
        raise GitError(f"ger show does not support revision ranges: {arg!r}")

    sha = git_out("rev-parse", "--verify", resolved, cwd=cwd)
    raw = git_out("log", "-1", "--format=%B", sha, cwd=cwd)
    summary = git_out("log", "-1", "--format=%s", sha, cwd=cwd)
    short = git_out("log", "-1", "--format=%h", sha, cwd=cwd)
    change_id = parse_change_id(raw)
    if not change_id:
        raise GitError(f"no Change-Id in commit {short}")
    row = CommitStatusInput(sha=sha, short_sha=short, summary=summary, change_id=change_id)
    return ShowCommitResolution(row=row, is_local_commit=True)
