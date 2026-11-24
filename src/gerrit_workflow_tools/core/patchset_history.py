"""Whether Gerrit's patchset is in a local commit's history: what tells a **diverged** commit from one ahead.

CONTEXT.md **diverged**, ADR-0015. A patchset is in the history of a local commit when the commit
was built on it: the patchset is reachable from the commit, from a position a branch holding the
commit had (its reflog), or from ger push's marker for that branch (``lastPush/<branch>``); or ger
took it over into a commit of that history (``ger checkout --update`` merging your edits into it,
recorded as ``refs/ger/taken/<patchset>``). Having the patchset's commit object is not enough: a
fetch, a refused update or ``ger review`` brings it without anything being built on it.

The one owner of ``refs/ger/taken/``. Nothing here asks Gerrit.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from gerrit_workflow_tools.core.git_run import git
from gerrit_workflow_tools.core.git_state import LAST_PUSH_PREFIX, Worktree, is_bookkeeping_branch


TAKEN_PREFIX = "refs/ger/taken/"
"""``refs/ger/taken/<patchset>`` points at the commit ger built on that patchset (shared by every worktree)."""


def record_taken(cwd: Path | str, patchset: str, commit: str, *, why: str) -> None:
    """Record that *commit* was built on Gerrit's *patchset* although the patchset is not its ancestor.

    *why* goes into the ref's log, which keeps every commit ever recorded for the patchset.
    """
    git("update-ref", "--create-reflog", "-m", why, f"{TAKEN_PREFIX}{patchset}", commit, cwd=cwd)


def built_on(cwd: Path | str | None, pairs: Iterable[tuple[str, str]]) -> frozenset[str]:
    """Of the ``(local commit, Gerrit patchset)`` *pairs*, the patchsets that are in their local commit's history.

    Three git calls however many pairs there are, none for none. The walk stops at the upstream of
    each branch holding the commits: an open change's patchset is never on it.
    """
    wanted = sorted({(local.strip().lower(), patchset.strip().lower()) for local, patchset in pairs})
    if not wanted:
        return frozenset()
    local = sorted({sha for sha, _ in wanted})
    patchsets = sorted({patchset for _, patchset in wanted})
    holders = _holders(cwd, local)
    marks = [f"refs/heads/{LAST_PUSH_PREFIX}{ref.removeprefix('refs/heads/')}" for ref, _ in holders]
    names = [*(ref for ref, _ in holders), *marks, *(f"{TAKEN_PREFIX}{p}" for p in patchsets)]
    logged = git("log", "-g", "--ignore-missing", "--format=%gD %H", *names, cwd=cwd, check=False)
    entries: list[str] = []
    taken: dict[str, set[str]] = {}
    for selector, _, sha in (line.partition(" ") for line in logged.stdout.splitlines() if logged.returncode == 0):
        ref = selector.rpartition("@{")[0]
        if ref.startswith(TAKEN_PREFIX):
            taken.setdefault(ref.removeprefix(TAKEN_PREFIX), set()).add(sha)
        else:
            entries.append(sha)
    # Without an upstream the walk stops below the local commits: what is under them is shared history.
    stops = [upstream for _, upstream in holders if upstream] or [f"{sha}^@" for sha in local]
    walked = git(
        "rev-list",
        "--ignore-missing",
        "--stdin",
        cwd=cwd,
        input="\n".join([*local, *entries, *names[: len(holders) * 2], "--not", *stops]) + "\n",
        check=False,
    )
    history = set(walked.stdout.split()) if walked.returncode == 0 else set(local)
    return frozenset(p for p in patchsets if p in history or taken.get(p, set()) & history)


def _holders(cwd: Path | str | None, local: list[str]) -> list[tuple[str, str]]:
    """``(refs/heads/<branch>, its upstream ref or "")`` for each branch holding one of *local*.

    During a rebase the commits already replayed are on no branch yet: the branch being rebased holds them.
    """
    fmt = "--format=%(refname)%00%(upstream)"
    found = git("for-each-ref", fmt, *(f"--contains={sha}" for sha in local), "refs/heads/", cwd=cwd, check=False)
    if found.returncode != 0 or not found.stdout.strip():
        rebasing = Worktree.from_cwd(cwd).rebasing_branch()
        found = git("for-each-ref", fmt, f"refs/heads/{rebasing}", cwd=cwd, check=False) if rebasing else found
    rows = (line.split("\0") for line in found.stdout.splitlines() if found.returncode == 0 and line)
    return [(ref, upstream) for ref, upstream in rows if not is_bookkeeping_branch(ref.removeprefix("refs/heads/"))]
