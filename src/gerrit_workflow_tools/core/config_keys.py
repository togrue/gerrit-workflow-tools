"""The git config keys ger owns: how git spells them, where they are set, their defaults.

Pure names and constants, no git: :mod:`gerrit_workflow_tools.core.config` reads the values,
:mod:`gerrit_workflow_tools.core.config_form` offers them for editing, both from here.
"""

from __future__ import annotations

from enum import Enum


class ConfigScope(Enum):
    """Where an effective git config value was defined (``git config --show-scope``)."""

    SYSTEM = "system"
    GLOBAL = "global"
    LOCAL = "local"
    WORKTREE = "worktree"
    COMMAND = "command"

    @property
    def flag(self) -> str | None:
        """The ``git config`` option for this scope (``--global``, …); ``None`` for ``git -c``."""
        return None if self is ConfigScope.COMMAND else f"--{self.value}"


TRUTHY = frozenset({"1", "true", "yes", "on"})
DEFAULT_STOP_PATTERN = r"^(?:dropme!|todo\b|test!|wip\b)"
DEFAULT_WARNING_PATTERN = r"(?:^[^\s]+$|(?i:\b(?:wip|todo)\b))"
REMOTE_POLICIES = ("ignore-not-rebased", "warn-not-rebased", "error-not-rebased")
DEFAULT_REMOTE_POLICY = "ignore-not-rebased"
DEFAULT_SCRIPTS_DIR = ".ger"
DEFAULT_WRAP_WIDTH = 120
SHOW_AFTER_EDIT_KEY = "ger.showAfterEdit"


def canonical_cfg_key(key: str) -> str:
    """Match key normalization used in `git config --list` (last segment lowercased)."""
    if "." not in key:
        return key.lower()
    head, tail = key.rsplit(".", 1)
    return f"{head}.{tail.lower()}"
