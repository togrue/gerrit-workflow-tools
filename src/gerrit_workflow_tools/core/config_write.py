"""Mutations of git config keys owned by ger.

:class:`~gerrit_workflow_tools.core.config.Settings` is a read snapshot; these helpers
write. Any Settings taken before a write still reports the old value — take a fresh
snapshot if the new one has to be visible.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from gerrit_workflow_tools.core.config_keys import ConfigScope, current_key, gerrit_host_key, legacy_key
from gerrit_workflow_tools.core.git_run import git


@dataclass(frozen=True)
class ConfigWrite:
    """A git config change to apply after the ``ger config`` editor closes."""

    key: str
    action: Literal["set", "unset"]
    value: str = ""
    scope: ConfigScope = ConfigScope.LOCAL


def set_branch_config(
    cwd: Path | str | None,
    branch: str,
    *,
    gerrit_reviewers: str | None = None,
) -> None:
    """Write branch-scoped Gerrit settings via ``git config``."""
    if gerrit_reviewers is not None:
        git("config", f"branch.{branch}.gerReviewers", gerrit_reviewers, cwd=cwd)


def apply_config_write(cwd: Path | str | None, write: ConfigWrite) -> None:
    """Apply one scoped set/unset (``ger config``, ``ger setup``).

    The key's pre-``ger.*`` name is removed at the same scope either way: a set moves it
    to the new name, and an unset must not let the old name answer in its place.
    """
    flag = None if write.scope is ConfigScope.SYSTEM else write.scope.flag
    if flag is None:
        raise ValueError(f"cannot write {write.key} at scope {write.scope.value}")
    old = legacy_key(write.key)
    if old is not None:
        git("config", flag, "--unset-all", old, cwd=cwd, check=False)
    if write.action == "unset":
        git("config", flag, "--unset-all", write.key, cwd=cwd, check=False)
        return
    git("config", flag, write.key, write.value, cwd=cwd)


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


@dataclass(frozen=True)
class MovedKey:
    """One old key name :func:`migrate_legacy_keys` moved to its ``ger`` name."""

    old: str
    new: str
    scope: ConfigScope
    dropped: bool
    """The new name was set already at that scope: it wins, so the old one was only removed."""


def migrate_legacy_keys(cwd: Path | str | None) -> list[MovedKey]:
    """Move every old key name (:data:`~gerrit_workflow_tools.core.config_keys.LEGACY_KEYS`, the
    ``branch.<name>.gerrit*`` ones) to its ``ger`` name, in local and global config.

    Every value moves (a multi-valued key keeps them all) unless the new name is set at that scope,
    which then wins. System config and ``git -c`` are not written.
    """
    moved: list[MovedKey] = []
    for scope in (ConfigScope.LOCAL, ConfigScope.GLOBAL):
        flag = scope.flag or ""
        listed = git("config", flag, "--name-only", "--list", cwd=cwd, check=False)
        if listed.returncode != 0:  # no repository for --local, or no global file
            continue
        for old in dict.fromkeys(listed.stdout.split()):
            new = current_key(old)
            if new is None:
                continue
            dropped = git("config", flag, "--get-all", new, cwd=cwd, check=False).returncode == 0
            if not dropped:
                values = git("config", flag, "-z", "--get-all", old, cwd=cwd).stdout.split("\0")[:-1]
                for value in values:
                    git("config", flag, "--add", new, value, cwd=cwd)
            git("config", flag, "--unset-all", old, cwd=cwd, check=False)
            moved.append(MovedKey(legacy_key(new) or old, new, scope, dropped))
    return moved


def scope_unscoped_secrets(
    cwd: Path | str | None, web_url: str, scopes: tuple[ConfigScope, ...] = (ConfigScope.LOCAL, ConfigScope.GLOBAL)
) -> list[MovedKey]:
    """Move ``ger.token`` and ``ger.password`` (or their old names) to ``ger.<web_url>.<name>`` at each
    of *scopes*: an unscoped secret is sent to whatever host ``ger.webUrl`` names (#111). Where the
    host has its own at that scope already, that one wins and the unscoped one is only removed.
    """
    moved: list[MovedKey] = []
    for scope in scopes:
        flag = scope.flag or ""
        for name in ("token", "password"):
            new = gerrit_host_key(web_url, name)
            for old in filter(None, (f"ger.{name}", legacy_key(f"ger.{name}"))):
                got = git("config", flag, "--get", old, cwd=cwd, check=False)
                value = (got.stdout or "").strip() if got.returncode == 0 else ""
                if not value:
                    continue
                dropped = git("config", flag, "--get", new, cwd=cwd, check=False).returncode == 0
                if not dropped:
                    git("config", flag, new, value, cwd=cwd)
                git("config", flag, "--unset-all", old, cwd=cwd, check=False)
                moved.append(MovedKey(old, new, scope, dropped))
    return moved
