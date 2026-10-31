"""The git/Gerrit settings snapshot — one effective ``git config --list``, read once.

Settings only. Anything that asks what the repository currently looks like (branch, HEAD,
upstream, rebase state) lives in :mod:`gerrit_workflow_tools.core.git_state`, which imports
from here; this module never queries repository state, so the dependency stays one-way.

A :class:`Settings` is immutable and carries every key it will ever answer, so reading one
costs a single ``git config --list``. Build it once per command (``Settings.from_cwd``) and
pass it down; tests build one from a plain mapping (``Settings.from_map``) with no repository
and nothing to invalidate.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from gerrit_workflow_tools.core.git_run import git


logger = logging.getLogger(__name__)


class ConfigError(ValueError):
    """Required git configuration is missing or unusable.

    Subclasses :class:`ValueError` so pre-existing ``except ValueError`` handlers keep
    working; it exists so the CLI can tell "you have not configured this" apart from any
    other :class:`ValueError`, which would otherwise be reported as a config problem.
    """


# Git lowercases variable names in `git config --list` output (e.g. gerrit.webUrl -> gerrit.weburl).
_GERRIT_STOP_PATTERN_KEY = "gerrit.stopPattern"
_GERRIT_WARNING_PATTERN_KEY = "gerrit.warningPattern"
_DEFAULT_STOP_PATTERN = r"^(?:dropme!|todo\b|test!|wip\b)"
_DEFAULT_WARNING_PATTERN = r"(?:^[^\s]+$|(?i:\b(?:wip|todo)\b))"
_DEFAULT_SCRIPTS_DIR = ".ger"


_TRUTHY = ("1", "true", "yes", "on")
_REMOTE_POLICIES = ("error-not-rebased", "warn-not-rebased", "ignore-not-rebased")
_DEFAULT_REMOTE_POLICY = "ignore-not-rebased"
_DEFAULT_WRAP_WIDTH = 120
EDIT_NEXT_SHOW_KEY = "gerrit.editNextShow"


def _canonical_cfg_key(key: str) -> str:
    """Match key normalization used in `git config --list` (last segment lowercased)."""
    if "." not in key:
        return key.lower()
    head, tail = key.rsplit(".", 1)
    return f"{head}.{tail.lower()}"


def _load_git_config_map(cwd: Path | str | None) -> dict[str, str]:
    """Parse effective `git config --list` (all scopes); last value wins for each key."""
    p = git("config", "--list", cwd=cwd, check=False)
    single: dict[str, str] = {}
    if p.returncode != 0 or not p.stdout:
        return single
    for raw in p.stdout.splitlines():
        if not raw.strip() or "=" not in raw:
            continue
        k, v = raw.split("=", 1)
        single[_canonical_cfg_key(k)] = v
    return single


@dataclass(frozen=True)
class Settings:
    """Effective git/Gerrit settings for one repository, as of when it was read.

    Immutable on purpose: a value written after this snapshot was taken is not visible
    here, and the fix is to take a new snapshot rather than to invalidate a shared cache.
    """

    values: Mapping[str, str]

    @classmethod
    def from_cwd(cls, cwd: Path | str | None) -> Settings:
        """Read the effective configuration for *cwd* with one ``git config --list``."""
        return cls(values=_load_git_config_map(cwd))

    @classmethod
    def from_map(cls, raw: Mapping[str, str]) -> Settings:
        """Build a snapshot from literal ``key -> value`` pairs, keys canonicalized as git does."""
        return cls(values={_canonical_cfg_key(k): v for k, v in raw.items()})

    def get(self, key: str) -> str | None:
        """Return the value for *key*, stripped, or ``None`` when unset or blank.

        A whitespace-only value reads as unset, not as the empty string, so the return type
        is honest: every caller already treated ``""`` as absent.
        """
        v = self.values.get(_canonical_cfg_key(key))
        if v is None:
            return None
        stripped = v.strip()
        return stripped or None

    def flag(self, key: str, *, default: bool = False) -> bool:
        """Return whether *key* is truthy (``1``, ``true``, ``yes``, ``on``; case-insensitive)."""
        v = self.get(key)
        if v is None:
            return default
        return v.lower() in _TRUTHY

    # -- Gerrit host and identity ------------------------------------------------

    @property
    def gerrit_remote(self) -> str:
        """``gerrit.remote``, or ``origin``."""
        return self.get("gerrit.remote") or "origin"

    @property
    def gerrit_web_url(self) -> str | None:
        """``gerrit.webUrl``: HTTPS base URL (scheme + host, optional port, no path).

        Required for commands that call Gerrit HTTP (e.g. ``ger log``, ``ger show``).
        """
        return self.get("gerrit.webUrl")

    @property
    def gerrit_project(self) -> str | None:
        """``gerrit.project`` override, if set."""
        return self.get("gerrit.project")

    @property
    def scripts_dir(self) -> str:
        """``gerrit.scriptsDir``: project-local extension root (default ``.ger``).

        Relative values resolve from the repository toplevel. Absolute paths are allowed.
        Domain registries live at ``<scriptsDir>/<domain>/registry.py``.
        """
        return self.get("gerrit.scriptsDir") or _DEFAULT_SCRIPTS_DIR

    @property
    def gerrit_user(self) -> str | None:
        """``gerrit.user`` for HTTP Basic auth, if set."""
        return self.get("gerrit.user")

    @property
    def gerrit_password(self) -> str | None:
        """``gerrit.password`` for HTTP Basic auth, if set."""
        return self.get("gerrit.password")

    @property
    def gerrit_token(self) -> str | None:
        """``gerrit.token``, preferred over password for Basic auth, if set."""
        return self.get("gerrit.token")

    # -- Branch-scoped ------------------------------------------------------------

    def branch_gerrit_reviewers(self, branch: str) -> str | None:
        """``branch.<branch>.gerritReviewers`` (comma-separated list), if set."""
        return self.get(f"branch.{branch}.gerritReviewers")

    def branch_gerrit_target(self, branch: str) -> str | None:
        """``branch.<branch>.gerritTarget`` override for the Gerrit destination branch, if set."""
        return self.get(f"branch.{branch}.gerritTarget")

    def branch_attention_cursor(self, branch: str) -> str | None:
        """``branch.<branch>.gerAttentionCursor``: Change-Id walk position, if set."""
        return self.get(f"branch.{branch}.gerAttentionCursor")

    def branch_attention_cursor_at(self, branch: str) -> str | None:
        """``branch.<branch>.gerAttentionCursorAt``: UTC timestamp for the cursor, if set."""
        return self.get(f"branch.{branch}.gerAttentionCursorAt")

    @property
    def last_attention_change_id(self) -> str | None:
        """``ger.lastAttentionChangeId``: repo-level last walk Change-Id (lazy adopt)."""
        return self.get("ger.lastAttentionChangeId")

    @property
    def last_attention_change_id_at(self) -> str | None:
        """``ger.lastAttentionChangeIdAt``: UTC timestamp for :attr:`last_attention_change_id`."""
        return self.get("ger.lastAttentionChangeIdAt")

    @property
    def attention_cursor_ttl(self) -> str | None:
        """``ger.attentionCursorTtl`` raw value (e.g. ``14d``), or ``None`` for the default."""
        return self.get("ger.attentionCursorTtl")

    def remote_url(self, remote: str) -> str | None:
        """``remote.<remote>.url`` — the configured remote URL (not ``insteadOf``-rewritten)."""
        return self.get(f"remote.{remote}.url")

    def branch_remote(self, branch: str) -> str | None:
        """``branch.<branch>.remote`` when set."""
        return self.get(f"branch.{branch}.remote")

    def branch_merge(self, branch: str) -> str | None:
        """``branch.<branch>.merge`` when set (often ``refs/heads/<name>``)."""
        return self.get(f"branch.{branch}.merge")

    def branch_upstream_abbrev(self, branch: str) -> str | None:
        """Remote-tracking abbrev from ``branch.<branch>.remote`` + ``merge``.

        Returns ``origin/main`` only when ``merge`` is ``refs/heads/…`` (what
        ``git branch --set-upstream-to`` writes). Any other ``merge`` shape returns
        ``None`` so callers fall back to ``git rev-parse …@{upstream}``.
        """
        remote = self.branch_remote(branch)
        merge = self.branch_merge(branch)
        if not remote or not merge or remote == ".":
            return None
        prefix = "refs/heads/"
        if not merge.startswith(prefix):
            return None
        branch_name = merge[len(prefix) :]
        if not branch_name:
            return None
        return f"{remote}/{branch_name}"

    # -- Per-command defaults -----------------------------------------------------

    @property
    def log_defaults(self) -> dict[str, bool]:
        """Defaults for ``ger log`` from ``gerrit.log*`` keys (CLI flags override when passed)."""
        return {
            "show_url": self.flag("gerrit.logShowUrl"),
            "show_change_id": self.flag("gerrit.logShowChangeId"),
        }

    @property
    def push_defaults(self) -> dict[str, bool]:
        """Defaults for ``ger push`` from ``gerrit.push*`` keys, plus ``gerrit.lastPushedBranch``."""
        return {
            "show_attributes": self.flag("gerrit.pushShowAttributes"),
            "last_pushed_branch": self.flag("gerrit.lastPushedBranch", default=True),
        }

    @property
    def rebase_defaults(self) -> dict[str, bool]:
        """Defaults for ``ger rebase`` from ``gerrit.rebase*`` keys (CLI flags override when passed)."""
        return {
            "onto_remote": self.flag("gerrit.rebaseOntoRemote"),
            "drop_merged_equivalent": self.flag("gerrit.rebaseDropMergedEquivalent"),
        }

    @property
    def edit_next_show(self) -> bool:
        """``gerrit.editNextShow``: run ``ger show`` after a successful ``ger edit --next``.

        Unset is off. ``ger edit --next`` only moves unless this flag is set.
        """
        return self.flag(EDIT_NEXT_SHOW_KEY)

    @property
    def inbox_require_verified(self) -> bool:
        """``inbox.requireVerified``: CI gate for ready chains. Default on."""
        return self.flag("inbox.requireVerified", default=True)

    @property
    def inbox_verified_label(self) -> str:
        """``inbox.verifiedLabel``: CI label name. Default ``Verified``."""
        return self.get("inbox.verifiedLabel") or "Verified"

    @property
    def inbox_projects(self) -> list[str]:
        """``inbox.projects``: default ``--project`` list, comma-separated."""
        raw = self.get("inbox.projects")
        if not raw:
            return []
        return [part.strip() for part in raw.split(",") if part.strip()]

    @property
    def inbox_to_review_query(self) -> str | None:
        """``inbox.toReviewQuery``: wholesale override of the *to review* query."""
        return self.get("inbox.toReviewQuery")

    @property
    def inbox_limit(self) -> int | None:
        """``inbox.limit``: default ``--limit``, or ``None`` when unset/invalid."""
        raw = self.get("inbox.limit")
        if not raw:
            return None
        try:
            value = int(raw)
        except ValueError:
            return None
        return value if value >= 1 else None

    @property
    def push_remote_policy(self) -> str:
        """``gerrit.push.remotePolicy``: how to treat a branch not linearly on the fetched target tip.

        Values: ``ignore-not-rebased`` (default), ``warn-not-rebased``, ``error-not-rebased``.
        Unset, empty, or unknown values use ``ignore-not-rebased``.
        """
        v = self.get("gerrit.push.remotePolicy")
        if not v:
            return _DEFAULT_REMOTE_POLICY
        s = v.lower()
        return s if s in _REMOTE_POLICIES else _DEFAULT_REMOTE_POLICY

    # -- Commit-message patterns ---------------------------------------------------

    @property
    def stop_pattern(self) -> str:
        """``gerrit.stopPattern`` regex, or the built-in default when unset."""
        return self.get(_GERRIT_STOP_PATTERN_KEY) or _DEFAULT_STOP_PATTERN

    @property
    def warning_pattern(self) -> str:
        """``gerrit.warningPattern`` regex, or the built-in default when unset."""
        return self.get(_GERRIT_WARNING_PATTERN_KEY) or _DEFAULT_WARNING_PATTERN

    @property
    def wrap_width(self) -> int:
        """``gerrit.wrapWidth``: max width of ``ger show`` prose. Default 120.

        Unset, blank, non-numeric, or values below 1 use the default. The
        chain URL is not wrapped; comment text, context lines, the path
        header, and the commit message are.
        """
        raw = self.get("gerrit.wrapWidth")
        if not raw:
            return _DEFAULT_WRAP_WIDTH
        try:
            value = int(raw)
        except ValueError:
            return _DEFAULT_WRAP_WIDTH
        return value if value >= 1 else _DEFAULT_WRAP_WIDTH


def set_branch_config(
    cwd: Path | str | None,
    branch: str,
    *,
    gerrit_reviewers: str | None = None,
) -> None:
    """Write branch-scoped Gerrit settings via ``git config``.

    Any :class:`Settings` read before this call still reports the old value; take a fresh
    snapshot if the new one has to be visible.
    """
    if gerrit_reviewers is not None:
        git("config", f"branch.{branch}.gerritReviewers", gerrit_reviewers, cwd=cwd)


def set_attention_cursor_config(
    cwd: Path | str | None,
    branch: str,
    *,
    change_id: str | None,
    touched_at: str | None,
) -> None:
    """Write or clear ``branch.<branch>.gerAttentionCursor`` and ``…CursorAt``."""
    cursor_key = f"branch.{branch}.gerAttentionCursor"
    at_key = f"branch.{branch}.gerAttentionCursorAt"
    if change_id is None:
        git("config", "--unset-all", cursor_key, cwd=cwd, check=False)
        git("config", "--unset-all", at_key, cwd=cwd, check=False)
        return
    git("config", cursor_key, change_id, cwd=cwd)
    if touched_at is not None:
        git("config", at_key, touched_at, cwd=cwd)


def set_last_attention_change_id(
    cwd: Path | str | None,
    *,
    change_id: str | None,
    touched_at: str | None = None,
) -> None:
    """Write or clear repo-local ``ger.lastAttentionChangeId`` and ``…At`` for lazy adopt."""
    if change_id is None:
        git("config", "--unset-all", "ger.lastAttentionChangeId", cwd=cwd, check=False)
        git("config", "--unset-all", "ger.lastAttentionChangeIdAt", cwd=cwd, check=False)
        return
    git("config", "ger.lastAttentionChangeId", change_id, cwd=cwd)
    if touched_at is not None:
        git("config", "ger.lastAttentionChangeIdAt", touched_at, cwd=cwd)
