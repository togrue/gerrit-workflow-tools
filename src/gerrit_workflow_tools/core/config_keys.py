"""The git config keys ger owns: how git spells them, their former names, their defaults.

Every key ger owns is ``ger.<key>``, or ``branch.<name>.ger<Key>`` for a branch (#113). Before
that they were spread over ``gerrit.*``, ``inbox.*`` and ``branch.<name>.gerrit*``;
:data:`LEGACY_KEYS` is the one table of those renames. Reading falls back to the old name
(:class:`~gerrit_workflow_tools.core.config.Settings`); nothing writes it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
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


_CURRENT_BY_OLD_CANON = {canonical_cfg_key(old): new for new, old in LEGACY_KEYS.items()}
_CURRENT_BRANCH_KEYS = {old.lower(): new for new, old in _LEGACY_BRANCH_KEYS.items()}


def current_key(old: str) -> str | None:
    """The ``ger`` name of a key whose old name is *old* (as git lists it, or as written), or ``None``."""
    new = _CURRENT_BY_OLD_CANON.get(canonical_cfg_key(old))
    if new is not None:
        return new
    head, _, tail = old.rpartition(".")
    if head.startswith("branch.") and tail.lower() in _CURRENT_BRANCH_KEYS:
        return f"{head}.{_CURRENT_BRANCH_KEYS[tail.lower()]}"
    return None


MIGRATE_COMMAND = "ger config --migrate"


def legacy_keys_standing_in(
    values: Mapping[str, str], scopes: Mapping[str, ConfigScope]
) -> dict[str, ConfigScope | None]:
    """Every old key name *values* (canonical keys, as git lists them) answer from because the new
    name is unset or blank: the old name as written, with its scope."""

    def is_set(canon: str) -> bool:
        return bool(values.get(canon, "").strip())

    found: dict[str, ConfigScope | None] = {}
    for canon in values:
        new = current_key(canon)
        if new is not None and is_set(canon) and not is_set(canonical_cfg_key(new)):
            found[legacy_key(new) or canon] = scopes.get(canon)
    return found


def legacy_keys_warning(moves: Mapping[str, ConfigScope | None]) -> str:
    """One line for the old key names a snapshot still answers from (old name -> its scope).

    Names on disk are moved by one command; a ``git -c`` one has nothing on disk to move.
    """
    on_disk = [old for old, scope in moves.items() if scope is not None and scope.flag is not None]
    on_command_line = [f"{old} -> {current_key(old)}" for old in moves if old not in on_disk]
    parts = []
    if on_disk:
        parts.append(f"old git config key{'s' if len(on_disk) > 1 else ''} {', '.join(on_disk)}: run {MIGRATE_COMMAND}")
    if on_command_line:
        parts.append(f"rename git -c {', '.join(on_command_line)}")
    return "; ".join(parts)


@dataclass(frozen=True)
class LegacyKeysNotice:
    """What to say about the old key names a configuration still answers from, and the fix."""

    message: str
    """:func:`legacy_keys_warning`'s line: what each command logs once."""
    argv: tuple[str, ...] | None
    """The ger command that moves them (``config --migrate``, ``ger`` left out); ``None`` when none is on disk."""


def legacy_keys_notice(values: Mapping[str, str], scopes: Mapping[str, ConfigScope]) -> LegacyKeysNotice | None:
    """The notice for *values* (a snapshot's keys and scopes); ``None`` when no old name stands in."""
    moves = legacy_keys_standing_in(values, scopes)
    if not moves:
        return None
    on_disk = any(scope is not None and scope.flag is not None for scope in moves.values())
    return LegacyKeysNotice(legacy_keys_warning(moves), tuple(MIGRATE_COMMAND.split()[1:]) if on_disk else None)
