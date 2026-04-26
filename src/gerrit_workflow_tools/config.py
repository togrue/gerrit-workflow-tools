from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from gerrit_workflow_tools.git_run import GitError, git, git_out

logger = logging.getLogger(__name__)

# Git lowercases variable names in `git config --list` output (e.g. gerrit.webUrl -> gerrit.weburl).
_GERRIT_STOP_PATTERN_CANONICAL = "gerrit.stoppattern"
_GERRIT_WARNING_PATTERN_CANONICAL = "gerrit.warningpattern"

# In-memory snapshot: one `git config --list` per process per resolved cwd (lazy first access).
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
    """Parse `git config --list` once; last value wins for single-valued keys."""
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


def resolve_upstream_parsed(cwd: Path | str | None, branch: str | None = None) -> tuple[str, str] | None:
    """Parse ``@{upstream}`` into ``(remote_name, branch_after_first_slash)``.

    Uses the current branch's upstream (same as ``git rev-parse --abbrev-ref @{upstream}``).
    Returns ``None`` if there is no upstream or the abbrev-ref has no ``/``.
    """
    del branch  # reserved for future branch-specific upstream resolution
    p = git("rev-parse", "--abbrev-ref", "@{upstream}", cwd=cwd, check=False)
    if p.returncode != 0:
        return None
    upstream = p.stdout.strip()
    if "/" not in upstream:
        return None
    remote_name, rest = upstream.split("/", 1)
    return (remote_name, rest)


def effective_gerrit_destination_branch(cwd: Path | str | None, branch: str | None = None) -> str | None:
    """Gerrit destination for push/restack.

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
    p = git("rev-parse", "--abbrev-ref", "@{upstream}", cwd=cwd, check=False)
    if p.returncode != 0:
        return None
    return p.stdout.strip()


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


def infer_nearest_remote_tracking_branch(
    cwd: Path | str | None,
    head: str = "HEAD",
) -> tuple[str, int, int, int] | None:
    """Pick the remote-tracking ref with minimum symmetric divergence from *head*.

    For each ``refs/remotes/`` ref (excluding ``*/HEAD``), compute ``merge-base(head, ref)``
    then ``ahead = |mb..head|`` and ``behind = |mb..ref|``; minimize ``ahead + behind``,
    then *ahead*, then abbreviated ref name for stable tie-breaks.

    Returns ``(abbrev_ref, symmetric_total, ahead, behind)`` where *abbrev_ref* is suitable for
    ``git branch --set-upstream-to`` (e.g. ``origin/main``), or ``None`` if no candidate applies.
    """
    p = git("for-each-ref", "--format=%(refname)", "refs/remotes/", cwd=cwd, check=False)
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


def gpush_defaults(cwd: Path | str | None) -> dict[str, bool]:
    """Defaults for ``ger push`` from ``gerrit.push*`` keys.

    Includes ``gerrit.lastPushedBranch`` (overridden by
    ``--update-last-pushed``).
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


def head_is_linear_on_remote_gerrit_target(cwd: Path | str | None, branch: str | None = None) -> tuple[bool, str]:
    """Return whether ``HEAD`` contains the remote target tip (linear stack).

    After ``git fetch``, this is equivalent to ``merge-base(HEAD, R) == R`` for *R* the target tip, and to
    ``git merge-base --is-ancestor R HEAD`` (the fetched target tip must be an ancestor of ``HEAD``).

    Returns ``(ok, onto_ref)`` where *onto_ref* is the symbolic remote ref (e.g. ``origin/main``).
    """
    onto = resolve_rebase_onto_remote_ref(cwd, branch)
    p = git("merge-base", "--is-ancestor", onto, "HEAD", cwd=cwd, check=False)
    ok = p.returncode == 0
    logger.debug(
        "head_is_linear_on_remote_gerrit_target: onto=%r linear=%s (merge-base --is-ancestor rc=%s)",
        onto,
        ok,
        p.returncode,
    )
    return (ok, onto)


def rebase_defaults(cwd: Path | str | None) -> dict[str, bool]:
    """Defaults for ``ger restack`` from ``gerrit.rebase*`` keys (CLI flags override when passed)."""
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


def resolve_local_base_ref(cwd: Path | str | None, branch: str | None = None) -> tuple[str, str]:
    """
    Return ``(resolved_tip_commit, display_name)`` for the configured Gerrit push destination.

    Order: ``branch.gerritTarget`` -> ``@{upstream}``. Use this when you need the same
    **destination ref** as push (override or upstream), not for enumerating the local stack
    (see :func:`~gerrit_workflow_tools.stack.upstream_tracking_tip_and_display`).

    ``gerritTarget`` must be the Gerrit destination **branch name** (e.g. ``dev``). It must
    resolve to an existing ref—usually a local branch or ``refs/remotes/<remote>/<branch>``
    after ``git fetch``.
    """
    b = branch or current_branch(cwd)
    target = branch_gerrit_target(cwd, b)
    if target:
        p = git("rev-parse", "--verify", target, cwd=cwd, check=False)
        if p.returncode != 0:
            p = git("rev-parse", "--verify", f"refs/heads/{target}", cwd=cwd, check=False)
        if p.returncode == 0:
            return (p.stdout.strip(), target)
        raise GitError(
            f"gerritTarget '{target}' is configured but does not resolve to a local "
            "ref (needed for stack / merge-base). "
            f"Fetch from your Gerrit remote (`gerrit.remote`, often `origin`), e.g. "
            f"`git fetch <remote>` or `git fetch <remote> <branch>`, so the branch exists as a "
            f"remote-tracking ref when required. "
            f"Set branch.{b}.gerritTarget to the destination branch name on Gerrit (e.g. `dev`); "
            f"do not create a *local* branch whose name is `origin/<branch>`—that form should only "
            f"appear as `refs/remotes/<remote>/<branch>` after fetching."
        )

    upstream_name = git("rev-parse", "--abbrev-ref", "@{upstream}", cwd=cwd, check=False)
    if upstream_name.returncode == 0:
        upstream = upstream_name.stdout.strip()
        upstream_ref = git("rev-parse", "--verify", upstream, cwd=cwd, check=False)
        if upstream_ref.returncode == 0:
            return (upstream_ref.stdout.strip(), upstream)

    raise GitError(
        f"No base branch found for '{b}'.\n"
        "Set an upstream, e.g.:\n"
        f"  git branch --set-upstream-to=<remote>/<branch>\n"
        "Or infer the nearest remote-tracking branch and set upstream:\n"
        "  ger branch infer-upstream\n"
        "Optional per-branch Gerrit destination overrides: see `ger branch --help`."
    )


def _remote_tracking_ref_candidates_from_target(remote_name: str, target: str) -> list[str]:
    """Build refs to try for ``ger restack --onto-remote`` from ``branch.*.gerritTarget``.

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

    Unlike :func:`resolve_local_base_ref`, this returns a **remote-tracking symbolic ref**, not a
    detached SHA, so ``git rebase`` replays local commits onto the remote tip.

    Uses the same effective Gerrit destination as :func:`effective_gerrit_destination_branch`
    (``gerritTarget`` override, or upstream when its remote is ``gerrit.remote``). There is no
    fallback to ``<gerrit.remote>/main`` when neither is available.
    """
    b = branch or current_branch(cwd)
    remote_name = gerrit_remote(cwd)
    eff = effective_gerrit_destination_branch(cwd, b)
    if not eff:
        raise GitError(
            f"No Gerrit destination branch for `ger restack --onto-remote` on branch {b!r}. "
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
    raise GitError(f"No remote-tracking ref found for `ger restack --onto-remote` (tried {tried}). {hint}")
