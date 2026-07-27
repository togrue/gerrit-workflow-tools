"""Read and normalize git/Gerrit *settings* — one effective ``git config --list`` per cwd.

Settings only. Anything that asks what the repository currently looks like (branch, HEAD,
upstream, rebase state) lives in :mod:`gerrit_workflow_tools.core.git_state`, which imports
from here; this module never queries repository state, so the dependency stays one-way.
"""

from __future__ import annotations

import logging
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

# In-memory snapshot: one effective `git config --list` per process per cwd (lazy first access).
_snapshot: dict[str, str] | None = None  # pylint: disable=invalid-name
_snapshot_cwd: str | None = None  # pylint: disable=invalid-name


def clear_gerrit_git_config_cache() -> None:
    """Drop cached config so the next read loads from git again."""
    global _snapshot, _snapshot_cwd  # pylint: disable=global-statement
    _snapshot = None
    _snapshot_cwd = None


def _canonical_cfg_key(key: str) -> str:
    """Match key normalization used in `git config --list` (last segment lowercased)."""
    if "." not in key:
        return key.lower()
    head, tail = key.rsplit(".", 1)
    return f"{head}.{tail.lower()}"


def _resolve_cwd_key(cwd: Path | str | None) -> str:
    p = Path.cwd() if cwd is None else Path(cwd)
    return str(p.resolve())


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
        ck = _canonical_cfg_key(k)
        single[ck] = v
    return single


def _ensure_snapshot(cwd: Path | str | None) -> None:
    global _snapshot, _snapshot_cwd  # pylint: disable=global-statement
    key = _resolve_cwd_key(cwd)
    if _snapshot is not None and _snapshot_cwd == key:
        return
    _snapshot = _load_git_config_map(cwd)
    _snapshot_cwd = key


def _config_get(cwd: Path | str | None, key: str) -> str | None:
    _ensure_snapshot(cwd)
    assert _snapshot is not None
    ck = _canonical_cfg_key(key)
    v = _snapshot.get(ck)
    return v.strip() if v else None


def branch_gerrit_reviewers(cwd: Path | str | None, branch: str) -> str | None:
    """Return ``branch.<branch>.gerritReviewers`` (comma-separated list), if set."""
    return _config_get(cwd, f"branch.{branch}.gerritReviewers")


def branch_gerrit_target(cwd: Path | str | None, branch: str) -> str | None:
    """Return ``branch.<branch>.gerritTarget`` override for the Gerrit destination branch, if set."""
    return _config_get(cwd, f"branch.{branch}.gerritTarget")


def gerrit_remote(cwd: Path | str | None) -> str:
    """Return ``gerrit.remote`` or ``origin``."""
    v = _config_get(cwd, "gerrit.remote")
    return v or "origin"


def gerrit_web_url(cwd: Path | str | None) -> str | None:
    """Gerrit HTTPS base URL (scheme + host, optional port, no path).

    Required for commands that call Gerrit HTTP (e.g. ``ger log``,
    ``ger show``).
    """
    return _config_get(cwd, "gerrit.webUrl")


def gerrit_project(cwd: Path | str | None) -> str | None:
    """Return explicit ``gerrit.project`` override, if set."""
    return _config_get(cwd, "gerrit.project")


def gerrit_user(cwd: Path | str | None) -> str | None:
    """Return ``gerrit.user`` for HTTP Basic auth, if set."""
    return _config_get(cwd, "gerrit.user")


def gerrit_password(cwd: Path | str | None) -> str | None:
    """Return ``gerrit.password`` for HTTP Basic auth, if set."""
    return _config_get(cwd, "gerrit.password")


def gerrit_token(cwd: Path | str | None) -> str | None:
    """Return ``gerrit.token`` (preferred over password for Basic auth), if set."""
    return _config_get(cwd, "gerrit.token")


def gshow_comment_tail_lines(cwd: Path | str | None) -> int:
    """Return ``gerrit.showCommentTailLines``.

    Must be a positive integer; defaults to ``10`` if unset or invalid.
    """
    v = _config_get(cwd, "gerrit.showCommentTailLines")
    if not v:
        return 10
    try:
        n = int(v.strip())
    except ValueError:
        return 10
    if n < 1:
        return 10
    return n


def config_bool(cwd: Path | str | None, key: str, *, default: bool = False) -> bool:
    """Return whether ``git config`` *key* is truthy.

    Truthy values: ``1``, ``true``, ``yes``, ``on`` (case-insensitive).
    """
    v = _config_get(cwd, key)
    if v is None or not str(v).strip():
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def log_defaults(cwd: Path | str | None) -> dict[str, bool]:
    """Defaults for ``ger log`` from ``gerrit.log*`` keys (CLI flags override when passed)."""
    return {
        "show_url": config_bool(cwd, "gerrit.logShowUrl"),
        "show_change_id": config_bool(cwd, "gerrit.logShowChangeId"),
    }


def ger_push_defaults(cwd: Path | str | None) -> dict[str, bool]:
    """Defaults for ``ger push`` from ``gerrit.push*`` keys.

    Includes ``gerrit.lastPushedBranch``.
    """
    return {
        "show_attributes": config_bool(cwd, "gerrit.pushShowAttributes"),
        "last_pushed_branch": config_bool(cwd, "gerrit.lastPushedBranch", default=True),
    }


def gerrit_push_remote_policy(cwd: Path | str | None) -> str:
    """Return ``gerrit.push.remotePolicy``: how to treat a branch not linearly on the fetched Gerrit target tip.

    Values: ``ignore-not-rebased`` (default), ``warn-not-rebased``, ``error-not-rebased``.
    Unset, empty, or unknown values use ``ignore-not-rebased``.
    """
    v = _config_get(cwd, "gerrit.push.remotePolicy")
    if not v:
        return "ignore-not-rebased"
    s = v.strip().lower()
    if s in ("error-not-rebased", "warn-not-rebased", "ignore-not-rebased"):
        return s
    return "ignore-not-rebased"


def rebase_defaults(cwd: Path | str | None) -> dict[str, bool]:
    """Defaults for ``ger rebase`` from ``gerrit.rebase*`` keys (CLI flags override when passed)."""
    return {
        "onto_remote": config_bool(cwd, "gerrit.rebaseOntoRemote"),
        "drop_merged_equivalent": config_bool(cwd, "gerrit.rebaseDropMergedEquivalent"),
    }


def stop_pattern(cwd: Path | str | None) -> str:
    """Return ``gerrit.stopPattern`` regex, or the built-in default when unset."""
    configured = _config_get(cwd, _GERRIT_STOP_PATTERN_KEY)
    return configured if configured else _DEFAULT_STOP_PATTERN


def warning_pattern(cwd: Path | str | None) -> str:
    """Return ``gerrit.warningPattern`` regex, or the built-in default when unset."""
    configured = _config_get(cwd, _GERRIT_WARNING_PATTERN_KEY)
    return configured if configured else _DEFAULT_WARNING_PATTERN


def set_branch_config(
    cwd: Path | str | None,
    branch: str,
    *,
    gerrit_reviewers: str | None = None,
) -> None:
    """Write branch-scoped Gerrit settings via ``git config`` and clear the config cache."""
    if gerrit_reviewers is not None:
        git("config", f"branch.{branch}.gerritReviewers", gerrit_reviewers, cwd=cwd)
    clear_gerrit_git_config_cache()
