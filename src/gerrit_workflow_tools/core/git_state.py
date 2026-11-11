"""Query the repository's current state: branch, HEAD, rebase, upstream, push destination.

Sits one layer above :mod:`gerrit_workflow_tools.core.config`: everything here runs ``git``
to ask what the repo looks like right now, and some of it consults settings to interpret the
answer (e.g. whether the upstream's remote is ``gerrit.remote``). The dependency runs one way
— ``config`` never asks about repository state.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.git_run import GitError, abs_cwd, git, git_out


logger = logging.getLogger(__name__)

_WORKTREE_LOCK = threading.Lock()
_WORKTREE_BY_CWD: dict[str, Worktree] = {}


@dataclass(frozen=True)
class Worktree:
    """Live repository identity from one ``git rev-parse`` (toplevel, HEAD branch, git-dir)."""

    toplevel: Path | None
    checked_out_branch: str | None
    """Named branch at HEAD, or ``None`` when detached (``abbrev-ref`` is ``HEAD``)."""
    git_dir: Path | None

    @classmethod
    def from_cwd(cls, cwd: Path | str | None) -> Worktree:
        cwd_abs = abs_cwd(cwd)
        with _WORKTREE_LOCK:
            cached = _WORKTREE_BY_CWD.get(cwd_abs)
            if cached is not None:
                return cached
            loaded = cls._load(cwd, cwd_abs)
            _WORKTREE_BY_CWD[cwd_abs] = loaded
            return loaded

    @classmethod
    def _load(cls, cwd: Path | str | None, cwd_abs: str) -> Worktree:
        p = git(
            "rev-parse",
            "--show-toplevel",
            "--abbrev-ref",
            "HEAD",
            "--git-dir",
            cwd=cwd,
            check=False,
        )
        if p.returncode != 0 or not (p.stdout or "").strip():
            return cls(toplevel=None, checked_out_branch=None, git_dir=None)
        lines = [line.strip() for line in p.stdout.splitlines() if line.strip()]
        top_raw = lines[0] if lines else ""
        abbrev = lines[1] if len(lines) > 1 else ""
        git_dir_raw = lines[2] if len(lines) > 2 else ""
        toplevel = Path(top_raw) if top_raw else None
        checked = abbrev if abbrev and abbrev != "HEAD" else None
        git_dir = Path(cwd_abs) / git_dir_raw if git_dir_raw else None
        return cls(toplevel=toplevel, checked_out_branch=checked, git_dir=git_dir)


def clear_worktree_cache() -> None:
    """Drop memoized :class:`Worktree` snapshots (tests and :func:`clear_git_cache`)."""
    with _WORKTREE_LOCK:
        _WORKTREE_BY_CWD.clear()


def repo_toplevel(cwd: Path | str | None) -> Path | None:
    """Return ``git rev-parse --show-toplevel``, or ``None`` outside a work tree."""
    return Worktree.from_cwd(cwd).toplevel


def current_branch(cwd: Path | str | None) -> str:
    """Return the current branch name (``git rev-parse --abbrev-ref HEAD``)."""
    wt = Worktree.from_cwd(cwd)
    if wt.toplevel is None:
        return git_out("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd)
    return wt.checked_out_branch or "HEAD"


def is_detached_head(cwd: Path | str | None) -> bool:
    """True when HEAD is checked out directly (not via a branch)."""
    wt = Worktree.from_cwd(cwd)
    if wt.toplevel is None:
        return git("symbolic-ref", "-q", "HEAD", cwd=cwd, check=False).returncode != 0
    return wt.checked_out_branch is None


def checked_out_branch_name(cwd: Path | str | None) -> str | None:
    """Named branch checked out, or ``None`` when HEAD is detached."""
    wt = Worktree.from_cwd(cwd)
    if wt.toplevel is None:
        p = git("branch", "--show-current", cwd=cwd, check=False)
        if p.returncode != 0:
            return None
        return (p.stdout or "").strip() or None
    return wt.checked_out_branch


def git_dir(cwd: Path | str | None) -> Path | None:
    """The repository's git dir, also when HEAD is unborn; ``None`` outside a repository."""
    wt = Worktree.from_cwd(cwd)
    if wt.toplevel is not None:
        return wt.git_dir
    p = git("rev-parse", "--git-dir", cwd=cwd, check=False)
    raw = (p.stdout or "").strip()
    return Path(abs_cwd(cwd)) / raw if p.returncode == 0 and raw else None


def rebase_in_progress(cwd: Path | str | None) -> bool:
    """True while git has a rebase underway, including one stopped at a commit."""
    repo_dir = git_dir(cwd)
    return repo_dir is not None and any((repo_dir / d).exists() for d in ("rebase-merge", "rebase-apply"))


def rebase_refusal(cwd: Path | str | None) -> str | None:
    """Why a command that moves branches cannot start during a rebase, or ``None``."""
    if rebase_in_progress(cwd):
        return "a rebase is already in progress; run `git rebase --continue` or `git rebase --abort` first"
    return None


def history_rewrite_refusal(cwd: Path | str | None) -> str | None:
    """Why rewriting the local stack cannot start here, or ``None``: the one wording every rewrite shares.

    Git refuses an interactive rebase over a rebase underway or uncommitted changes to tracked
    files; asking first replaces git's bare refusal with what to do about it.
    """
    if refusal := rebase_refusal(cwd):
        return refusal
    if git_out("status", "--porcelain", "--untracked-files=no", cwd=cwd):
        return (
            "you have uncommitted changes; commit them (`ger fix REV` folds them into a stack commit) "
            "or `git stash` them first"
        )
    return None


def rebase_in_progress_branch(cwd: Path | str | None) -> str | None:
    """Branch currently being rebased, if Git has an in-progress rebase state."""
    repo_dir = git_dir(cwd)
    if repo_dir is None:
        return None
    for state_dir in ("rebase-merge", "rebase-apply"):
        head_name = repo_dir / state_dir / "head-name"
        if not head_name.exists():
            continue
        try:
            branch = head_name.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        return branch.removeprefix("refs/heads/") or None
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


def _push_context_branch_rank(cwd: Path | str | None, branch: str, *, settings: Settings) -> tuple[int, int, str]:
    """Lower is better when choosing a branch for detached-HEAD push config."""
    from gerrit_workflow_tools.core.upstream_interactive import branch_has_upstream

    mode = ger_push_mode(cwd, branch, settings=settings)
    mode_rank = 0 if mode == "gerrit" else (1 if mode == "vanilla" else 2)
    return (mode_rank, 0 if branch_has_upstream(cwd, branch) else 1, branch)


def resolve_working_branch(cwd: Path | str | None, *, settings: Settings) -> str | None:
    """Branch whose mode and ``branch.<name>.*`` config apply while HEAD may be detached.

    Uses the checked-out branch when present. During rebase, uses Git's recorded rebased
    branch. Otherwise on detached HEAD, picks a local branch that points at ``HEAD``
    (preferring Gerrit upstream, then any upstream).
    """
    checked = checked_out_branch_name(cwd)
    if checked:
        return checked
    rebasing = rebase_in_progress_branch(cwd)
    if rebasing:
        return rebasing
    candidates = _branches_pointing_at_head(cwd)
    if not candidates:
        return None
    return min(candidates, key=lambda b: _push_context_branch_rank(cwd, b, settings=settings))


def refs_for_push_branch_name(target: str, *, settings: Settings) -> str:
    """Branch segment for Gerrit ``refs/for/<branch>``.

    When *target* is ``<remote>/<branch>`` and *remote* equals ``gerrit.remote``,
    returns *branch* only (e.g. ``origin/dev`` → ``dev``). Otherwise returns *target*
    unchanged (e.g. ``main``, ``release/1.0``).
    """
    return target.removeprefix(f"{settings.gerrit_remote}/")


def upstream_abbrev_sym(cwd: Path | str | None, branch: str | None = None) -> str | None:
    """Revision expression for a branch's upstream, or ``HEAD``'s when on a named branch."""
    if branch:
        return f"{branch}@{{upstream}}"
    if is_detached_head(cwd):
        return None
    return "@{upstream}"


def resolve_upstream_abbrev_ref(
    cwd: Path | str | None,
    branch: str | None = None,
    *,
    settings: Settings | None = None,
) -> str | None:
    """Return ``git rev-parse --abbrev-ref`` of the upstream, or ``None``."""
    if branch and settings is not None:
        configured = settings.branch_upstream_abbrev(branch)
        if configured:
            return configured
    sym = upstream_abbrev_sym(cwd, branch)
    if sym is None:
        return None
    p = git("rev-parse", "--abbrev-ref", sym, cwd=cwd, check=False)
    if p.returncode != 0:
        return None
    return p.stdout.strip() or None


def resolve_upstream_parsed(
    cwd: Path | str | None,
    branch: str | None = None,
    *,
    settings: Settings | None = None,
) -> tuple[str, str] | None:
    """Parse upstream into ``(remote_name, branch_after_first_slash)``.

    Uses *branch*'s upstream when given; otherwise ``@{upstream}`` for a checked-out branch.
    Returns ``None`` if there is no upstream or the abbrev-ref has no ``/``.
    """
    upstream = resolve_upstream_abbrev_ref(cwd, branch, settings=settings)
    if not upstream or "/" not in upstream:
        return None
    remote_name, rest = upstream.split("/", 1)
    return (remote_name, rest)


def resolve_branch_for_branch_config(cwd: Path | str | None, branch: str | None = None, *, settings: Settings) -> str:
    """Branch whose ``branch.<name>.*`` settings apply, defaulting to the working branch."""
    if branch is not None:
        return branch
    return resolve_working_branch(cwd, settings=settings) or current_branch(cwd)


def effective_gerrit_destination_branch(
    cwd: Path | str | None, branch: str | None = None, *, settings: Settings
) -> str | None:
    """Gerrit destination for push/rebase from branch config or upstream.

    Prefers ``branch.<name>.gerritTarget`` when set; otherwise returns upstream ref
    when its remote matches ``gerrit.remote`` (e.g. ``origin/main``), suitable
    for :func:`refs_for_push_branch_name`. Returns ``None`` when neither is available.
    """
    branch_name = resolve_branch_for_branch_config(cwd, branch, settings=settings)
    override = settings.branch_gerrit_target(branch_name)
    if override:
        return override
    parsed = resolve_upstream_parsed(cwd, branch_name, settings=settings)
    if parsed is None or parsed[0] != settings.gerrit_remote:
        return None
    return "/".join(parsed)


def ger_push_mode(
    cwd: Path | str | None, branch: str | None = None, *, settings: Settings
) -> Literal["gerrit", "vanilla"] | None:
    """Return push mode for current branch destination.

    ``gerrit`` uses ``refs/for/…``, ``vanilla`` uses plain ``git push``.
    Returns ``None`` when destination cannot be determined.
    """
    parsed = resolve_upstream_parsed(cwd, branch, settings=settings)
    if not parsed:
        return None
    return "gerrit" if parsed[0] == settings.gerrit_remote else "vanilla"


def head_is_linear_on_remote_gerrit_target(
    cwd: Path | str | None,
    branch: str | None = None,
    *,
    head: str = "HEAD",
    settings: Settings,
) -> tuple[bool, str]:
    """Return whether *head* contains the remote target tip (linear stack).

    After ``git fetch``, this is equivalent to ``merge-base(head, R) == R`` for *R* the target tip, and to
    ``git merge-base --is-ancestor R head`` (the fetched target tip must be an ancestor of *head*).

    Returns ``(ok, onto_ref)`` where *onto_ref* is the symbolic remote ref (e.g. ``origin/main``).
    """
    onto = resolve_rebase_onto_remote_ref(cwd, branch, settings=settings)
    p = git("merge-base", "--is-ancestor", onto, head, cwd=cwd, check=False)
    ok = p.returncode == 0
    logger.debug(
        "head_is_linear_on_remote_gerrit_target: onto=%r linear=%s (merge-base --is-ancestor rc=%s)",
        onto,
        ok,
        p.returncode,
    )
    return (ok, onto)


def _remote_tracking_ref(remote_name: str, target: str) -> str:
    """The ref ``ger rebase --onto-remote`` tries for the upstream *target*.

    Accepts a bare branch name (``dev`` → ``<remote>/dev``) or an existing remote-tracking
    form (``origin/dev``) without doubling the remote (``origin/origin/dev``).
    ``refs/remotes/origin/dev`` is normalized to ``origin/dev``.
    """
    ref = target.strip().removeprefix("refs/remotes/")
    return ref if "/" in ref else f"{remote_name}/{ref}"


def resolve_rebase_onto_remote_ref(cwd: Path | str | None, branch: str | None = None, *, settings: Settings) -> str:
    """
    Return a ref accepted by ``git rebase -i <ref>`` for rebasing onto the **latest fetched**
    remote-tracking tip of the configured Gerrit target branch (e.g. ``origin/main``).

    Uses the same effective Gerrit destination as :func:`effective_gerrit_destination_branch`
    (upstream when its remote is ``gerrit.remote``). There is no fallback to
    ``<gerrit.remote>/main`` when upstream is unavailable.
    """
    b = branch or current_branch(cwd)
    remote_name = settings.gerrit_remote
    eff = effective_gerrit_destination_branch(cwd, b, settings=settings)
    if not eff:
        raise GitError(
            f"No Gerrit destination branch for `ger rebase --onto-remote` on branch {b!r}. "
            f"Set upstream to a branch on `{remote_name}` (gerrit.remote), "
            f"e.g. `git branch --set-upstream-to={remote_name}/<branch>` after `git fetch`. "
            f"Fetch so `refs/remotes/{remote_name}/<branch>` exists."
        )

    ref = _remote_tracking_ref(remote_name, eff)
    logger.debug("resolve_rebase_onto_remote_ref: branch=%r remote=%r effective=%r ref=%s", b, remote_name, eff, ref)
    if git("rev-parse", "--verify", ref, cwd=cwd, check=False).returncode == 0:
        return ref

    hint = (
        f"Fetch from your Gerrit remote (`gerrit.remote`, often `{remote_name}`), e.g. "
        f"`git fetch {remote_name}` so `refs/remotes/{remote_name}/<branch>` exists."
    )
    raise GitError(f"No remote-tracking ref found for `ger rebase --onto-remote` (tried {ref}). {hint}")
