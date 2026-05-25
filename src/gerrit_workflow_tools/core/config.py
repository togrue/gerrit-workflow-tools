"""Read and normalize git/Gerrit configuration values."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from gerrit_workflow_tools.core.git_run import GitError, git, git_out

logger = logging.getLogger(__name__)

# Git lowercases variable names in `git config --list` output (e.g. gerrit.webUrl -> gerrit.weburl).
_GERRIT_STOP_PATTERN_CANONICAL = "gerrit.stoppattern"
_GERRIT_WARNING_PATTERN_CANONICAL = "gerrit.warningpattern"

# In-memory snapshot: one effective `git config --list` per process per cwd (lazy first access).
_snapshot: dict[str, str] | None = None  # pylint: disable=invalid-name
_snapshot_multi: dict[str, list[str]] | None = None  # pylint: disable=invalid-name
_snapshot_cwd: str | None = None  # pylint: disable=invalid-name


def clear_gerrit_git_config_cache() -> None:
    """Drop cached config so the next read loads from git again."""
    global _snapshot, _snapshot_multi, _snapshot_cwd  # pylint: disable=global-statement
    _snapshot = None
    _snapshot_multi = None
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


def _load_git_config_maps(cwd: Path | str | None) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Parse effective `git config --list` (all scopes); last value wins for single-valued keys."""
    p = git("config", "--list", cwd=cwd, check=False)
    single: dict[str, str] = {}
    multi: dict[str, list[str]] = {}
    if p.returncode != 0 or not p.stdout:
        return single, multi
    for raw in p.stdout.splitlines():
        if not raw.strip() or "=" not in raw:
            continue
        k, v = raw.split("=", 1)
        ck = _canonical_cfg_key(k)
        if ck in (_GERRIT_STOP_PATTERN_CANONICAL, _GERRIT_WARNING_PATTERN_CANONICAL):
            multi.setdefault(ck, []).append(v)
        else:
            single[ck] = v
    return single, multi


def _ensure_snapshot(cwd: Path | str | None) -> None:
    global _snapshot, _snapshot_multi, _snapshot_cwd  # pylint: disable=global-statement
    key = _resolve_cwd_key(cwd)
    if _snapshot is not None and _snapshot_cwd == key:
        return
    s, m = _load_git_config_maps(cwd)
    _snapshot = s
    _snapshot_multi = m
    _snapshot_cwd = key


def _config_get(cwd: Path | str | None, key: str) -> str | None:
    _ensure_snapshot(cwd)
    assert _snapshot is not None
    ck = _canonical_cfg_key(key)
    if ck in (_GERRIT_STOP_PATTERN_CANONICAL, _GERRIT_WARNING_PATTERN_CANONICAL):
        return None
    v = _snapshot.get(ck)
    return v.strip() if v else None


def current_branch(cwd: Path | str | None) -> str:
    """Return the current branch name (``git rev-parse --abbrev-ref HEAD``)."""
    return git_out("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd)


def is_detached_head(cwd: Path | str | None) -> bool:
    """True when HEAD is checked out directly (not via a branch)."""
    return git("symbolic-ref", "-q", "HEAD", cwd=cwd, check=False).returncode != 0


def checked_out_branch_name(cwd: Path | str | None) -> str | None:
    """Named branch checked out, or ``None`` when HEAD is detached."""
    p = git("branch", "--show-current", cwd=cwd, check=False)
    if p.returncode != 0:
        return None
    name = (p.stdout or "").strip()
    return name or None


def _git_dir(cwd: Path | str | None) -> Path | None:
    p = git("rev-parse", "--git-dir", cwd=cwd, check=False)
    if p.returncode != 0:
        return None
    raw = (p.stdout or "").strip()
    if not raw:
        return None
    git_dir = Path(raw)
    if git_dir.is_absolute():
        return git_dir
    base = Path.cwd() if cwd is None else Path(cwd)
    return base / git_dir


def rebase_in_progress_branch(cwd: Path | str | None) -> str | None:
    """Branch currently being rebased, if Git has an in-progress rebase state."""
    git_dir = _git_dir(cwd)
    if git_dir is None:
        return None
    for state_dir in ("rebase-merge", "rebase-apply"):
        head_name = git_dir / state_dir / "head-name"
        if not head_name.exists():
            continue
        try:
            branch = head_name.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if branch.startswith("refs/heads/"):
            branch = branch[len("refs/heads/") :]
        return branch or None
    return None


def _branches_pointing_at_head(cwd: Path | str | None) -> list[str]:
    p = git("branch", "--points-at", "HEAD", cwd=cwd, check=False)
    if p.returncode != 0:
        return []
    names: list[str] = []
    for line in (p.stdout or "").splitlines():
        name = line.strip().lstrip("* ").strip()
        if not name or name.startswith("("):
            continue
        names.append(name)
    return names


def _push_context_branch_rank(cwd: Path | str | None, branch: str) -> tuple[int, int, int, str]:
    """Lower is better when choosing a branch for detached-HEAD push config."""
    from gerrit_workflow_tools.core.upstream_interactive import branch_has_upstream

    mode = ger_push_mode(cwd, branch)
    mode_rank = 0 if mode == "gerrit" else (1 if mode == "vanilla" else 2)
    return (
        0 if branch_gerrit_target(cwd, branch) else 1,
        mode_rank,
        0 if branch_has_upstream(cwd, branch) else 1,
        branch,
    )


def resolve_working_branch(cwd: Path | str | None) -> str | None:
    """Best branch for commands that need branch config while HEAD may be detached."""
    checked = checked_out_branch_name(cwd)
    if checked:
        return checked
    rebasing = rebase_in_progress_branch(cwd)
    if rebasing:
        return rebasing
    candidates = _branches_pointing_at_head(cwd)
    if not candidates:
        return None
    return min(candidates, key=lambda b: _push_context_branch_rank(cwd, b))


def resolve_push_context_branch(cwd: Path | str | None) -> str | None:
    """Branch name for ``ger push`` mode and ``branch.<name>.*`` config.

    Uses the checked-out branch when present. During rebase, uses Git's recorded
    rebased branch. Otherwise on detached HEAD, picks a local branch that points at
    ``HEAD`` (preferring ``gerritTarget``, then Gerrit upstream, then any upstream).
    """
    return resolve_working_branch(cwd)


def branch_gerrit_target(cwd: Path | str | None, branch: str | None = None) -> str | None:
    """Return ``branch.<name>.gerritTarget`` (optional override for Gerrit destination), if set."""
    b = branch or current_branch(cwd)
    key = f"branch.{b}.gerritTarget"
    return _config_get(cwd, key)


def branch_gerrit_reviewers(cwd: Path | str | None, branch: str | None = None) -> str | None:
    """Return ``branch.<name>.gerritReviewers`` (comma-separated list), if set."""
    b = branch or current_branch(cwd)
    return _config_get(cwd, f"branch.{b}.gerritReviewers")


def gerrit_remote(cwd: Path | str | None) -> str:
    """Return ``gerrit.remote`` or ``origin``."""
    v = _config_get(cwd, "gerrit.remote")
    return v or "origin"


def refs_for_push_branch_name(cwd: Path | str | None, target: str) -> str:
    """Branch segment for Gerrit ``refs/for/<branch>``.

    When *target* is ``<remote>/<branch>`` and *remote* equals :func:`gerrit_remote`,
    returns *branch* only (e.g. ``origin/dev`` → ``dev``). Otherwise returns *target*
    unchanged (e.g. ``main``, ``release/1.0``).
    """
    r = gerrit_remote(cwd)
    prefix = f"{r}/"
    if target.startswith(prefix):
        return target[len(prefix) :]
    return target


def upstream_abbrev_sym(cwd: Path | str | None, branch: str | None = None) -> str | None:
    """Revision expression for a branch's upstream, or ``HEAD``'s when on a named branch."""
    if branch:
        return f"{branch}@{{upstream}}"
    if is_detached_head(cwd):
        return None
    return "@{upstream}"


def resolve_upstream_abbrev_ref(cwd: Path | str | None, branch: str | None = None) -> str | None:
    """Return ``git rev-parse --abbrev-ref`` of the upstream, or ``None``."""
    sym = upstream_abbrev_sym(cwd, branch)
    if sym is None:
        return None
    p = git("rev-parse", "--abbrev-ref", sym, cwd=cwd, check=False)
    if p.returncode != 0:
        return None
    upstream = p.stdout.strip()
    return upstream or None


def resolve_upstream_parsed(cwd: Path | str | None, branch: str | None = None) -> tuple[str, str] | None:
    """Parse upstream into ``(remote_name, branch_after_first_slash)``.

    Uses *branch*'s upstream when given; otherwise ``@{upstream}`` for a checked-out branch.
    Returns ``None`` if there is no upstream or the abbrev-ref has no ``/``.
    """
    upstream = resolve_upstream_abbrev_ref(cwd, branch)
    if not upstream or "/" not in upstream:
        return None
    remote_name, rest = upstream.split("/", 1)
    return (remote_name, rest)


def effective_gerrit_destination_branch(cwd: Path | str | None, branch: str | None = None) -> str | None:
    """Gerrit destination for push/rebase.

    Uses ``gerritTarget`` override, or upstream ref when its remote matches
    :func:`gerrit_remote`.

    Returns a value suitable for :func:`refs_for_push_branch_name` (e.g. ``main``, ``origin/main``).
    Returns ``None`` when there is no override and no upstream on the Gerrit remote.
    """
    override = branch_gerrit_target(cwd, branch)
    if override:
        return override
    parsed = resolve_upstream_parsed(cwd, branch)
    if not parsed:
        return None
    remote_name, _rest = parsed
    if remote_name != gerrit_remote(cwd):
        return None
    return resolve_upstream_abbrev_ref(cwd, branch)


def ger_push_mode(cwd: Path | str | None, branch: str | None = None) -> Literal["gerrit", "vanilla"] | None:
    """Return push mode for current branch destination.

    ``gerrit`` uses ``refs/for/…``, ``vanilla`` uses plain ``git push``.
    Returns ``None`` when destination cannot be determined.
    """
    if branch_gerrit_target(cwd, branch):
        return "gerrit"
    parsed = resolve_upstream_parsed(cwd, branch)
    if not parsed:
        return None
    remote_name, _rest = parsed
    if remote_name == gerrit_remote(cwd):
        return "gerrit"
    return "vanilla"


# pylint: disable=too-many-locals
def infer_nearest_remote_tracking_branch(
    cwd: Path | str | None,
    head: str = "HEAD",
) -> tuple[str, int, int, int] | None:
    """Pick the remote-tracking ref with minimum symmetric divergence from *head*.

    For each ref under the Gerrit remote's ``refs/remotes/<remote>/`` namespace
    (excluding ``*/HEAD``), compute ``merge-base(head, ref)`` then
    ``ahead = |mb..head|`` and ``behind = |mb..ref|``; minimize ``ahead + behind``,
    then *ahead*, then abbreviated ref name for stable tie-breaks.

    Returns ``(abbrev_ref, symmetric_total, ahead, behind)`` where *abbrev_ref* is suitable for
    ``git branch --set-upstream-to`` (e.g. ``origin/main``), or ``None`` if no candidate applies.
    """
    remote_ref_prefix = f"refs/remotes/{gerrit_remote(cwd)}/"
    p = git("for-each-ref", "--format=%(refname)", remote_ref_prefix, cwd=cwd, check=False)
    if p.returncode != 0 or not (p.stdout or "").strip():
        return None
    best_key: tuple[int, int, str] | None = None
    best_value: tuple[str, int, int, int] | None = None
    for line in (p.stdout or "").splitlines():
        ref = line.strip()
        if not ref or ref.endswith("/HEAD"):
            continue
        mb_p = git("merge-base", head, ref, cwd=cwd, check=False)
        if mb_p.returncode != 0:
            continue
        mb = mb_p.stdout.strip()
        ahead_p = git("rev-list", "--count", f"{mb}..{head}", cwd=cwd, check=False)
        behind_p = git("rev-list", "--count", f"{mb}..{ref}", cwd=cwd, check=False)
        if ahead_p.returncode != 0 or behind_p.returncode != 0:
            continue
        try:
            ahead = int(ahead_p.stdout.strip())
            behind = int(behind_p.stdout.strip())
        except ValueError:
            continue
        sym = ahead + behind
        abbrev_p = git("rev-parse", "--abbrev-ref", ref, cwd=cwd, check=False)
        if abbrev_p.returncode != 0:
            continue
        abbrev = abbrev_p.stdout.strip()
        key = (sym, ahead, abbrev)
        if best_key is None or key < best_key:
            best_key = key
            best_value = (abbrev, sym, ahead, behind)
    if best_value is None:
        return None
    return best_value


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


def head_is_linear_on_remote_gerrit_target(
    cwd: Path | str | None,
    branch: str | None = None,
    *,
    head: str = "HEAD",
) -> tuple[bool, str]:
    """Return whether *head* contains the remote target tip (linear stack).

    After ``git fetch``, this is equivalent to ``merge-base(head, R) == R`` for *R* the target tip, and to
    ``git merge-base --is-ancestor R head`` (the fetched target tip must be an ancestor of *head*).

    Returns ``(ok, onto_ref)`` where *onto_ref* is the symbolic remote ref (e.g. ``origin/main``).
    """
    onto = resolve_rebase_onto_remote_ref(cwd, branch)
    p = git("merge-base", "--is-ancestor", onto, head, cwd=cwd, check=False)
    ok = p.returncode == 0
    logger.debug(
        "head_is_linear_on_remote_gerrit_target: onto=%r linear=%s (merge-base --is-ancestor rc=%s)",
        onto,
        ok,
        p.returncode,
    )
    return (ok, onto)


def rebase_defaults(cwd: Path | str | None) -> dict[str, bool]:
    """Defaults for ``ger rebase`` from ``gerrit.rebase*`` keys (CLI flags override when passed)."""
    return {
        "onto_remote": config_bool(cwd, "gerrit.rebaseOntoRemote"),
        "drop_merged_equivalent": config_bool(cwd, "gerrit.rebaseDropMergedEquivalent"),
    }


def stop_patterns(cwd: Path | str | None) -> list[str]:
    """Return ``gerrit.stopPattern`` lines as regex strings, or built-in defaults if none are configured."""
    _ensure_snapshot(cwd)
    assert _snapshot_multi is not None
    lines = _snapshot_multi.get(_GERRIT_STOP_PATTERN_CANONICAL, [])
    lines = [ln.strip() for ln in lines if ln.strip()]
    if not lines:
        return [r"^dropme!", r"^todo\b", r"^test!", r"^wip\b"]
    return lines


def warning_patterns(cwd: Path | str | None) -> list[str]:
    """Return ``gerrit.warningPattern`` lines as regex strings, or built-in defaults if none are configured."""
    _ensure_snapshot(cwd)
    assert _snapshot_multi is not None
    lines = _snapshot_multi.get(_GERRIT_WARNING_PATTERN_CANONICAL, [])
    lines = [ln.strip() for ln in lines if ln.strip()]
    if not lines:
        return [r"^[^\s]+$", r"(?i:\bwip\b)", r"(?i:\btodo\b)"]
    return lines


def set_branch_config(
    cwd: Path | str | None,
    branch: str,
    *,
    gerrit_target: str | None = None,
    gerrit_reviewers: str | None = None,
) -> None:
    """Write branch-scoped Gerrit settings via ``git config`` and clear the config cache."""
    if gerrit_target is not None:
        git("config", f"branch.{branch}.gerritTarget", gerrit_target, cwd=cwd)
    if gerrit_reviewers is not None:
        git("config", f"branch.{branch}.gerritReviewers", gerrit_reviewers, cwd=cwd)
    clear_gerrit_git_config_cache()


def _remote_tracking_ref_candidates_from_target(remote_name: str, target: str) -> list[str]:
    """Build refs to try for ``ger rebase --onto-remote`` from ``branch.*.gerritTarget``.

    Accepts a bare branch name (``dev`` → ``<remote>/dev``) or an existing remote-tracking
    form (``origin/dev``) without doubling the remote (``origin/origin/dev``).
    ``refs/remotes/origin/dev`` is normalized to ``origin/dev``.
    """
    t = target.strip()
    if not t:
        return []
    if t.startswith("refs/remotes/"):
        t = t[len("refs/remotes/") :]
    if "/" in t:
        return [t]
    return [f"{remote_name}/{t}"]


def resolve_rebase_onto_remote_ref(cwd: Path | str | None, branch: str | None = None) -> str:
    """
    Return a ref accepted by ``git rebase -i <ref>`` for rebasing onto the **latest fetched**
    remote-tracking tip of the configured Gerrit target branch (e.g. ``origin/main``).

    Uses the same effective Gerrit destination as :func:`effective_gerrit_destination_branch`
    (``gerritTarget`` override, or upstream when its remote is ``gerrit.remote``). There is no
    fallback to ``<gerrit.remote>/main`` when neither is available.
    """
    b = branch or current_branch(cwd)
    remote_name = gerrit_remote(cwd)
    eff = effective_gerrit_destination_branch(cwd, b)
    if not eff:
        raise GitError(
            f"No Gerrit destination branch for `ger rebase --onto-remote` on branch {b!r}. "
            f"Set upstream to a branch on `{remote_name}` (gerrit.remote), "
            "e.g. `ger branch infer-upstream` after `git fetch`, or "
            f"`git branch --set-upstream-to={remote_name}/<branch>`. "
            f"Fetch so `refs/remotes/{remote_name}/<branch>` exists. "
            "Optional `gerritTarget` overrides: see `ger branch --help`."
        )

    candidates = _remote_tracking_ref_candidates_from_target(remote_name, eff)
    logger.debug(
        "resolve_rebase_onto_remote_ref: branch=%r remote=%r effective=%r candidates=%s",
        b,
        remote_name,
        eff,
        candidates,
    )
    seen: set[str] = set()
    for cand in candidates:
        if not cand or cand in seen:
            continue
        seen.add(cand)
        p = git("rev-parse", "--verify", cand, cwd=cwd, check=False)
        if p.returncode == 0:
            logger.debug("resolve_rebase_onto_remote_ref: using %r", cand)
            return cand

    tried = ", ".join(candidates) or f"{remote_name}/{eff}"
    hint = (
        f"Fetch from your Gerrit remote (`gerrit.remote`, often `{remote_name}`), e.g. "
        f"`git fetch {remote_name}` so `refs/remotes/{remote_name}/<branch>` exists."
    )
    raise GitError(f"No remote-tracking ref found for `ger rebase --onto-remote` (tried {tried}). {hint}")
