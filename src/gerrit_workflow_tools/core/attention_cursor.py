"""Attention cursor: walk position for iterative edit-attention rework.

Owns branch-local Change-Id cursor storage, TTL reset, lazy cross-branch adopt,
``.git/ger/attention-history.jsonl``, and pure target selection for resume / next / first.
Attention *reasons* stay in :mod:`gerrit_workflow_tools.core.attention`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from gerrit_workflow_tools.core.attention import (
    commit_needs_edit_attention,
    find_commit_by_change_id,
    first_commit_needing_edit_attention,
    next_commit_needing_edit_attention_after,
)
from gerrit_workflow_tools.core.config import Settings, set_attention_cursor_config, set_last_attention_change_id
from gerrit_workflow_tools.core.gerrit_change_status import LogCommit
from gerrit_workflow_tools.core.git_run import git
from gerrit_workflow_tools.core.git_state import Worktree


EditAttentionMode = Literal["resume", "next", "first"]
CursorAction = Literal["resume", "next", "first", "set", "adopt", "clear-ttl", "clear-missing"]

_HISTORY_NAME = "attention-history.jsonl"
_HISTORY_CAP = 200
_DEFAULT_TTL = timedelta(days=14)
_TTL_RE = re.compile(
    r"^\s*(?:(?P<weeks>\d+)\s*w)?(?:(?P<days>\d+)\s*d)?(?:(?P<hours>\d+)\s*h)?(?:(?P<minutes>\d+)\s*m)?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AttentionCursor:
    """Branch-local walk position: a Change-Id and when it was last touched."""

    change_id: str
    touched_at: datetime


@dataclass(frozen=True)
class CursorHistoryEvent:
    """One append-only history line under ``.git/ger/``."""

    ts: str
    branch: str
    change_id: str
    action: str
    sha: str | None = None


@dataclass(frozen=True)
class PrepareCursorResult:
    """Outcome of :func:`prepare_usable_cursor` (Change-Id may be absent)."""

    change_id: str | None
    notes: tuple[str, ...] = ()


def parse_attention_cursor_ttl(raw: str | None) -> timedelta:
    """Parse ``ger.attentionCursorTtl`` (e.g. ``14d``, ``2w``, ``48h``). Default 14 days."""
    if raw is None or not raw.strip():
        return _DEFAULT_TTL
    text = raw.strip()
    if text.isdigit():
        return timedelta(days=int(text))
    m = _TTL_RE.fullmatch(text)
    if not m:
        return _DEFAULT_TTL
    weeks = int(m.group("weeks") or 0)
    days = int(m.group("days") or 0)
    hours = int(m.group("hours") or 0)
    minutes = int(m.group("minutes") or 0)
    delta = timedelta(weeks=weeks, days=days, hours=hours, minutes=minutes)
    return delta if delta.total_seconds() > 0 else _DEFAULT_TTL


def _parse_iso_utc(raw: str | None) -> datetime | None:
    if not raw:
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def format_iso_utc(when: datetime) -> str:
    """Format *when* as a UTC ISO-8601 string with a ``Z`` suffix."""
    return when.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _format_iso_utc(when: datetime) -> str:
    return format_iso_utc(when)


def _now_utc(now: datetime | None) -> datetime:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc)


def _git_dir(cwd: Path | str | None) -> Path | None:
    """Resolve ``.git`` even when HEAD is unborn (empty repo)."""
    wt = Worktree.from_cwd(cwd)
    if wt.git_dir is not None:
        return wt.git_dir
    p = git("rev-parse", "--git-dir", cwd=cwd, check=False)
    if p.returncode != 0:
        return None
    raw = (p.stdout or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if path.is_absolute():
        return path
    base = Path(cwd) if cwd is not None else Path.cwd()
    return base / path


def history_path(cwd: Path | str | None) -> Path | None:
    """Path to ``.git/ger/attention-history.jsonl``, or ``None`` outside a git repo."""
    git_dir = _git_dir(cwd)
    if git_dir is None:
        return None
    return git_dir / "ger" / _HISTORY_NAME


def append_cursor_history(
    cwd: Path | str | None,
    *,
    branch: str,
    change_id: str,
    action: CursorAction,
    sha: str | None = None,
    now: datetime | None = None,
) -> None:
    """Append one history event; keep at most :data:`_HISTORY_CAP` lines."""
    path = history_path(cwd)
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "ts": _format_iso_utc(_now_utc(now)),
        "branch": branch,
        "change_id": change_id,
        "action": action,
    }
    if sha:
        event["sha"] = sha
    existing: list[str] = []
    if path.exists():
        try:
            existing = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            existing = []
    existing.append(json.dumps(event, separators=(",", ":")))
    if len(existing) > _HISTORY_CAP:
        existing = existing[-_HISTORY_CAP:]
    path.write_text("\n".join(existing) + "\n", encoding="utf-8")


def read_cursor_history(cwd: Path | str | None, *, limit: int = 50) -> list[CursorHistoryEvent]:
    """Return the newest *limit* history events (oldest first within the window)."""
    path = history_path(cwd)
    if path is None or not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    events: list[CursorHistoryEvent] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        change_id = raw.get("change_id")
        action = raw.get("action")
        branch = raw.get("branch")
        ts = raw.get("ts")
        if not isinstance(change_id, str) or not isinstance(action, str):
            continue
        if not isinstance(branch, str) or not isinstance(ts, str):
            continue
        sha = raw.get("sha")
        events.append(
            CursorHistoryEvent(
                ts=ts,
                branch=branch,
                change_id=change_id,
                action=action,
                sha=sha if isinstance(sha, str) else None,
            )
        )
    if limit > 0:
        events = events[-limit:]
    return events


def load_branch_cursor(settings: Settings, branch: str) -> AttentionCursor | None:
    """Read ``branch.<name>.gerAttentionCursor`` + ``…At`` from *settings*."""
    change_id = settings.branch_attention_cursor(branch)
    if not change_id:
        return None
    touched = _parse_iso_utc(settings.branch_attention_cursor_at(branch))
    if touched is None:
        return None
    return AttentionCursor(change_id=change_id, touched_at=touched)


def write_branch_cursor(
    cwd: Path | str | None,
    branch: str,
    change_id: str,
    *,
    action: CursorAction,
    sha: str | None = None,
    now: datetime | None = None,
) -> None:
    """Persist branch cursor, repo last Change-Id, and a history line."""
    when = _now_utc(now)
    stamp = _format_iso_utc(when)
    set_attention_cursor_config(cwd, branch, change_id=change_id, touched_at=stamp)
    set_last_attention_change_id(cwd, change_id=change_id, touched_at=stamp)
    append_cursor_history(cwd, branch=branch, change_id=change_id, action=action, sha=sha, now=when)


def clear_branch_cursor(
    cwd: Path | str | None,
    branch: str,
    *,
    change_id: str,
    action: Literal["clear-ttl", "clear-missing"],
    now: datetime | None = None,
) -> None:
    """Unset the branch cursor and record why."""
    set_attention_cursor_config(cwd, branch, change_id=None, touched_at=None)
    append_cursor_history(cwd, branch=branch, change_id=change_id, action=action, now=now)


def resolve_edit_attention_target(
    commits: Sequence[LogCommit],
    *,
    mode: EditAttentionMode,
    cursor_change_id: str | None = None,
    after_change_id: str | None = None,
) -> LogCommit | None:
    """Pick the edit-attention commit for *mode* without reading or writing storage."""
    ordered = list(commits)
    if mode == "first":
        return first_commit_needing_edit_attention(ordered)
    if mode == "next":
        after = after_change_id or cursor_change_id
        if not after:
            return first_commit_needing_edit_attention(ordered)
        return next_commit_needing_edit_attention_after(ordered, after)
    # resume
    if not cursor_change_id:
        return first_commit_needing_edit_attention(ordered)
    current = find_commit_by_change_id(ordered, cursor_change_id)
    if current is None:
        return first_commit_needing_edit_attention(ordered)
    if commit_needs_edit_attention(current):
        return current
    return next_commit_needing_edit_attention_after(ordered, cursor_change_id)


def prepare_usable_cursor(
    cwd: Path | str | None,
    *,
    branch: str,
    commits: Sequence[LogCommit],
    settings: Settings,
    now: datetime | None = None,
) -> PrepareCursorResult:
    """Load cursor for *branch*, applying TTL clear, missing clear, and lazy adopt.

    Mutates storage when clearing or adopting.
    """
    when = _now_utc(now)
    notes: list[str] = []
    cursor = load_branch_cursor(settings, branch)
    ttl = parse_attention_cursor_ttl(settings.attention_cursor_ttl)

    if cursor is not None and when - cursor.touched_at > ttl:
        clear_branch_cursor(cwd, branch, change_id=cursor.change_id, action="clear-ttl", now=when)
        # Drop repo last too so lazy adopt does not revive an expired walk.
        set_last_attention_change_id(cwd, change_id=None)
        notes.append(f"attention cursor expired (TTL {settings.attention_cursor_ttl or '14d'}); cleared")
        cursor = None
        # Re-read would still see the old last id in this Settings snapshot; skip adopt below.
        return PrepareCursorResult(change_id=None, notes=tuple(notes))

    if cursor is not None:
        if find_commit_by_change_id(commits, cursor.change_id) is None:
            clear_branch_cursor(cwd, branch, change_id=cursor.change_id, action="clear-missing", now=when)
            notes.append(f"attention cursor Change-Id {cursor.change_id} not in stack; cleared")
            cursor = None
        else:
            return PrepareCursorResult(change_id=cursor.change_id, notes=tuple(notes))

    last_id = settings.last_attention_change_id
    if last_id and find_commit_by_change_id(commits, last_id) is not None:
        write_branch_cursor(cwd, branch, last_id, action="adopt", now=when)
        notes.append(f"adopted attention cursor Change-Id {last_id} from last walk")
        return PrepareCursorResult(change_id=last_id, notes=tuple(notes))

    return PrepareCursorResult(change_id=None, notes=tuple(notes))
