"""Which upstream a branch has, which it should have, and setting it: no prompting here.

The suggestion is cheap: the branch's reflog says what it was created from, and one
``for-each-ref`` measures every remote branch's distance (git 2.41). ``upstream_interactive``
asks at a terminal; ``ger serve`` answers ``GET /stack/upstream``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from gerrit_workflow_tools.core.config import ConfigError, Settings
from gerrit_workflow_tools.core.git_run import UnknownRevisionError, commit_sha_or_none, git


logger = logging.getLogger(__name__)


class NeedsUpstreamError(Exception):
    """Failed only because *branch* has no upstream: a caller that can ask for one may set it and try again.

    *askable* is off where the command already asked, or decided not to (``ger push -y``).
    """

    def __init__(self, message: str, *, branch: str, askable: bool = True) -> None:
        super().__init__(message)
        self.branch = branch
        self.askable = askable


class MissingUpstreamError(NeedsUpstreamError, ConfigError):
    """*branch* has no upstream, and the command needs it (exit ``CONFIG``)."""


def missing_upstream_error(branch: str, *, askable: bool = True) -> MissingUpstreamError:
    """The one error, and the one hint, every command gives for a branch with no upstream."""
    return MissingUpstreamError(
        f"No upstream configured for branch {branch!r}.\n"
        "Set an upstream, e.g.:\n"
        "  git branch --set-upstream-to=<remote>/<branch>\n"
        "Fetch from your Gerrit remote first if the tracking branch is missing.",
        branch=branch,
        askable=askable,
    )


_RECENT_UPSTREAM_KEY = "gerrit.recentUpstreamAbbrev"
_RECENT_UPSTREAM_LIMIT = 20


def _divergence_at_once(cwd: Path | str | None, head: str, prefix: str) -> list[tuple[str, int, int]] | None:
    """``(abbrev, ahead, behind)`` of *head* against every ref under *prefix*, in one ``for-each-ref``.

    ``None`` when this git has no ``%(ahead-behind)`` (it came with 2.41).
    """
    fmt = f"--format=%(refname) %(refname:short) %(ahead-behind:{head})"
    p = git("for-each-ref", fmt, prefix, cwd=cwd, check=False)
    if p.returncode != 0:
        return None
    rows: list[tuple[str, int, int]] = []
    for line in p.stdout.splitlines():
        parts = line.split()
        if len(parts) != 4 or not (parts[2].isdigit() and parts[3].isdigit()):
            return None
        ref, abbrev, ref_only, head_only = parts
        if not ref.endswith("/HEAD"):
            rows.append((abbrev, int(head_only), int(ref_only)))
    return rows


def _divergence_one_by_one(cwd: Path | str | None, head: str, prefix: str) -> list[tuple[str, int, int]]:
    """:func:`_divergence_at_once` for an older git: one call per ancestor ref; up to three for others.

    Three calls up front: ``rev-parse`` for *head*, one ``for-each-ref`` naming every ref with its
    short name and commit, one more (``--merged``) listing the refs that are ancestors of *head*: their
    merge base is the ref itself, so they need no ``merge-base`` and are 0 behind. Every other ref costs
    a ``merge-base`` and a ``rev-list`` for its behind count (none when it contains *head*); the ahead
    count costs one ``rev-list`` per distinct merge base, shared by the refs that have it. Counts are
    ``<merge-base>..<head>`` and ``<merge-base>..<ref>``. A ref is named by ``%(refname:short)``, as on
    git 2.41+, also when it is a symbolic ref to another branch of the remote.
    """
    head_p = git("rev-parse", "-q", "--verify", f"{head}^{{commit}}", cwd=cwd, check=False)
    fmt = "--format=%(refname) %(refname:short) %(objectname)"
    p = git("for-each-ref", fmt, prefix, cwd=cwd, check=False)
    if head_p.returncode != 0 or p.returncode != 0:
        return []
    head_sha = head_p.stdout.strip()
    # An ancestor of head is its own (only) merge base with head.
    merged = git("for-each-ref", "--format=%(refname)", f"--merged={head_sha}", prefix, cwd=cwd, check=False)
    ancestors = set(merged.stdout.split()) if merged.returncode == 0 else set()

    def count(mb: str, tip: str) -> int | None:
        if mb == tip:
            return 0
        n = git("rev-list", "--count", f"{mb}..{tip}", cwd=cwd, check=False)
        return int(n.stdout.strip()) if n.returncode == 0 and n.stdout.strip().isdigit() else None

    ahead_by_base: dict[str, int | None] = {}
    rows: list[tuple[str, int, int]] = []
    for line in p.stdout.splitlines():
        parts = line.split()
        if len(parts) != 3 or parts[0].endswith("/HEAD"):
            continue
        ref, abbrev, ref_sha = parts
        if ref in ancestors:
            mb = ref_sha
        else:
            mb_p = git("merge-base", head_sha, ref_sha, cwd=cwd, check=False)
            if mb_p.returncode != 0:
                continue
            mb = mb_p.stdout.strip()
        if mb not in ahead_by_base:
            ahead_by_base[mb] = count(mb, head_sha)
        ahead, behind = ahead_by_base[mb], count(mb, ref_sha)
        if ahead is not None and behind is not None:
            rows.append((abbrev, ahead, behind))
    return rows


def infer_nearest_remote_tracking_branch(
    cwd: Path | str | None,
    head: str = "HEAD",
    *,
    settings: Settings,
) -> tuple[str, int, int, int] | None:
    """Pick the remote-tracking ref with minimum symmetric divergence from *head*.

    For each ref under the Gerrit remote's ``refs/remotes/<remote>/`` namespace
    (excluding ``*/HEAD``): ``ahead`` commits of *head* not in the ref, ``behind`` commits of
    the ref not in *head*; minimize ``ahead + behind``, then *ahead*, then abbreviated ref name
    for stable tie-breaks. One git call for all refs where git can (2.41), else one to three per ref.

    Returns ``(abbrev_ref, symmetric_total, ahead, behind)`` where *abbrev_ref* is suitable for
    ``git branch --set-upstream-to`` (e.g. ``origin/main``), or ``None`` if no candidate applies.
    """
    prefix = f"refs/remotes/{settings.gerrit_remote}/"
    rows = _divergence_at_once(cwd, head, prefix)
    if rows is None:
        rows = _divergence_one_by_one(cwd, head, prefix)
    if not rows:
        return None
    abbrev, ahead, behind = min(rows, key=lambda row: (row[1] + row[2], row[1], row[0]))
    return abbrev, ahead + behind, ahead, behind


def branch_created_from(cwd: Path | str | None, branch: str, *, settings: Settings) -> str | None:
    """The Gerrit remote's branch *branch* was created from (``origin/main``), as its reflog tells.

    ``git checkout -b topic origin/main`` records that; a branch created from ``FETCH_HEAD`` or a
    commit, or whose reflog expired, gives ``None``.
    """
    p = git("reflog", "show", "--format=%gs", f"refs/heads/{branch}", cwd=cwd, check=False)
    lines = p.stdout.splitlines() if p.returncode == 0 else []
    _, created, source = (lines[-1] if lines else "").partition("branch: Created from ")
    name = source.strip().removeprefix("refs/remotes/")
    if not created or not name.startswith(f"{settings.gerrit_remote}/"):
        return None
    found = git("rev-parse", "-q", "--verify", f"refs/remotes/{name}^{{commit}}", cwd=cwd, check=False)
    return name if found.returncode == 0 else None


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        item = value.strip()
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def branch_has_upstream(cwd: Path | str | None, branch: str) -> bool:
    p = git("rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}", cwd=cwd, check=False)
    return p.returncode == 0


def local_branch_exists(cwd: Path | str | None, branch: str) -> bool:
    return git("show-ref", "--verify", "--quiet", f"refs/heads/{branch}", cwd=cwd, check=False).returncode == 0


def read_recent_upstream_abbrevs(cwd: Path | str | None) -> list[str]:
    p = git("config", "--global", "--get-all", _RECENT_UPSTREAM_KEY, cwd=cwd, check=False)
    if p.returncode != 0:
        return []
    return _dedupe_preserve_order((p.stdout or "").splitlines())


def _write_recent_upstream_abbrevs(cwd: Path | str | None, values: list[str]) -> None:
    current = _dedupe_preserve_order(values)[:_RECENT_UPSTREAM_LIMIT]
    git("config", "--global", "--unset-all", _RECENT_UPSTREAM_KEY, cwd=cwd, check=False)
    for value in current:
        git("config", "--global", "--add", _RECENT_UPSTREAM_KEY, value, cwd=cwd)


def append_recent_upstream_abbrev(cwd: Path | str | None, abbrev: str) -> None:
    candidate = abbrev.strip()
    if not candidate:
        return
    resolved = git("rev-parse", "--verify", candidate, cwd=cwd, check=False)
    if resolved.returncode != 0:
        logger.debug("skip unresolved recent upstream candidate: %r", candidate)
        return
    recent = read_recent_upstream_abbrevs(cwd)
    _write_recent_upstream_abbrevs(cwd, [candidate, *recent])


def _remote_tracking_ref_abbrevs(cwd: Path | str | None) -> list[str]:
    # Not `refname:short`: it shortens `origin/HEAD` to `origin`, which then looks like a branch.
    p = git("for-each-ref", "--format=%(refname:lstrip=2)", "refs/remotes/", cwd=cwd, check=False)
    if p.returncode != 0:
        return []
    return _dedupe_preserve_order(
        [line.strip() for line in (p.stdout or "").splitlines() if line.strip() and not line.strip().endswith("/HEAD")]
    )


_CANDIDATE_LIMIT = 200


@dataclass(frozen=True)
class UpstreamChoice:
    """What to offer as the upstream of *branch*: the best guess, then recent picks, then every remote branch."""

    branch: str
    current: str | None
    """The upstream it has now, if any."""
    suggested: str | None
    why: str | None
    """Why *suggested*, in a few words."""
    candidates: list[str]
    """Suggested first, at most 200."""


def upstream_choice(cwd: Path | str | None, branch: str, *, settings: Settings) -> UpstreamChoice:
    """The upstream to offer *branch*: what it was created from, else the nearest branch of the Gerrit remote.

    Fast: the reflog line costs one git call, the nearest branch one more (git 2.41).
    """
    current = git("rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}", cwd=cwd, check=False)
    suggested, why = branch_created_from(cwd, branch, settings=settings), "the branch was created from it"
    if suggested is None:
        nearest = infer_nearest_remote_tracking_branch(cwd, branch, settings=settings)
        if nearest is not None:
            abbrev, _, ahead, behind = nearest
            suggested, why = abbrev, f"nearest: {ahead} ahead, {behind} behind"
    recent = read_recent_upstream_abbrevs(cwd)
    ordered = _dedupe_preserve_order([suggested or "", *recent, *_remote_tracking_ref_abbrevs(cwd)])
    return UpstreamChoice(
        branch=branch,
        current=current.stdout.strip() if current.returncode == 0 else None,
        suggested=suggested,
        why=why if suggested else None,
        candidates=ordered[:_CANDIDATE_LIMIT],
    )


def set_branch_upstream(cwd: Path | str | None, branch: str, upstream: str) -> None:
    """Make *upstream* (a remote-tracking branch, ``origin/main``) the upstream of *branch*, and remember it."""
    found = git("rev-parse", "--verify", "--quiet", f"refs/remotes/{upstream}", cwd=cwd, check=False)
    if found.returncode != 0:
        raise UnknownRevisionError(f"{upstream!r} is not a remote-tracking branch; fetch from the remote first")
    git("branch", "--set-upstream-to", upstream, branch, cwd=cwd)
    append_recent_upstream_abbrev(cwd, upstream)


def track_remote_branch(cwd: Path | str | None, branch: str, remote: str, target: str) -> bool:
    """Make ``<remote>/<target>`` the upstream of *branch* when that remote-tracking ref exists; whether it did."""
    if commit_sha_or_none(cwd, f"refs/remotes/{remote}/{target}") is None:
        return False
    git("branch", "-q", f"--set-upstream-to={remote}/{target}", branch, cwd=cwd)
    return True
