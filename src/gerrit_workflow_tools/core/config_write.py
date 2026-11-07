"""Mutations of git config keys owned by ger.

:class:`~gerrit_workflow_tools.core.config.Settings` is a read snapshot; these helpers
write. Any Settings taken before a write still reports the old value — take a fresh
snapshot if the new one has to be visible.
"""

from __future__ import annotations

from pathlib import Path

from gerrit_workflow_tools.core.git_run import git


def set_branch_config(
    cwd: Path | str | None,
    branch: str,
    *,
    gerrit_reviewers: str | None = None,
) -> None:
    """Write branch-scoped Gerrit settings via ``git config``."""
    if gerrit_reviewers is not None:
        git("config", f"branch.{branch}.gerritReviewers", gerrit_reviewers, cwd=cwd)


def _write_stamped(cwd: Path | str | None, key: str, value: str | None, touched_at: str | None) -> None:
    """Write *key* and its ``…At`` timestamp, or clear both when *value* is ``None``."""
    if value is None:
        git("config", "--unset-all", key, cwd=cwd, check=False)
        git("config", "--unset-all", f"{key}At", cwd=cwd, check=False)
        return
    git("config", key, value, cwd=cwd)
    if touched_at is not None:
        git("config", f"{key}At", touched_at, cwd=cwd)


def set_attention_cursor_config(
    cwd: Path | str | None,
    branch: str,
    *,
    change_id: str | None,
    touched_at: str | None,
) -> None:
    """Write or clear ``branch.<branch>.gerAttentionCursor`` and ``…CursorAt``."""
    _write_stamped(cwd, f"branch.{branch}.gerAttentionCursor", change_id, touched_at)


def set_last_attention_change_id(
    cwd: Path | str | None,
    *,
    change_id: str | None,
    touched_at: str | None = None,
) -> None:
    """Write or clear repo-local ``ger.lastAttentionChangeId`` and ``…At`` for lazy adopt."""
    _write_stamped(cwd, "ger.lastAttentionChangeId", change_id, touched_at)
