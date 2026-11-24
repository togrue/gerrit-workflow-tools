"""The git config keys ger owns: how git spells them, their former names, their defaults.

Every key ger owns is ``ger.<key>``, or ``branch.<name>.ger<Key>`` for a branch (#113). Before
that they were spread over ``gerrit.*``, ``inbox.*`` and ``branch.<name>.gerrit*``;
:data:`LEGACY_KEYS` is the one table of those renames. Reading falls back to the old name
(:class:`~gerrit_workflow_tools.core.config.Settings`); nothing writes it.
"""

from __future__ import annotations

from collections.abc import Mapping
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

_FORMERLY_GERRIT = (
    "remote",
    "webUrl",
    "allowInsecureHttp",
    "user",
    "password",
    "token",
    "project",
    "scriptsDir",
    "wrapWidth",
    "warningPattern",
    "stopPattern",
    "rebaseOntoRemote",
    "rebaseDropMergedEquivalent",
    "pushShowAttributes",
    "pushPublishDrafts",
    "lastPushedBranch",
    "logShowUrl",
    "logShowChangeId",
    "recentUpstreamAbbrev",
)
_FORMERLY_INBOX = ("requireVerified", "verifiedLabel", "projects", "toReviewQuery", "limit")

# New key -> the name it had before.
LEGACY_KEYS: Mapping[str, str] = {
    **{f"ger.{name}": f"gerrit.{name}" for name in _FORMERLY_GERRIT},
    "ger.pushRemotePolicy": "gerrit.push.remotePolicy",
    **{f"ger.inbox{name[0].upper()}{name[1:]}": f"inbox.{name}" for name in _FORMERLY_INBOX},
}
_LEGACY_BRANCH_KEYS = {"gerReviewers": "gerritReviewers", "gerTarget": "gerritTarget"}


def canonical_cfg_key(key: str) -> str:
    """Match key normalization used in `git config --list` (last segment lowercased)."""
    if "." not in key:
        return key.lower()
    head, tail = key.rsplit(".", 1)
    return f"{head}.{tail.lower()}"


_LEGACY_BY_CANON = {canonical_cfg_key(new): old for new, old in LEGACY_KEYS.items()}


def legacy_key(key: str) -> str | None:
    """The name *key* had before it moved to the ``ger`` namespace, or ``None``."""
    old = _LEGACY_BY_CANON.get(canonical_cfg_key(key))
    if old is not None:
        return old
    head, _, tail = key.rpartition(".")
    if head.startswith("branch.") and tail in _LEGACY_BRANCH_KEYS:
        return f"{head}.{_LEGACY_BRANCH_KEYS[tail]}"
    return None


def legacy_key_warning(old: str, new: str, scope: ConfigScope | None) -> str:
    """One line naming the ``git config`` commands that move *old* to *new* at *scope*."""
    if scope is None or scope.flag is None:  # `git -c`: nothing on disk to move
        return f"git config key {old} is deprecated; use {new}"
    git_config = f"git config {scope.flag}"
    return (
        f"git config key {old} is deprecated; move it with: "
        f'{git_config} {new} "$({git_config} {old})" && {git_config} --unset {old}'
    )
