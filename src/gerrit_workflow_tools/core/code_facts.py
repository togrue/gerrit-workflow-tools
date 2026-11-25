"""What the repository knows about the code of the commits an overlay compares: which exist here,
their patch-ids, and whether a local commit has the same code as Gerrit's current patch set."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from gerrit_workflow_tools.core.git_run import git


if TYPE_CHECKING:
    from gerrit_workflow_tools.core.gerrit.change_view import ChangeView


@dataclass(frozen=True)
class CodeFacts:
    """What the repository knows about the commits an overlay compares, read for all of them at once."""

    present: frozenset[str] = frozenset()
    """Which of the asked commits exist here."""
    patch_ids: dict[str, str] = field(default_factory=dict)
    """``git patch-id`` of each present single-parent commit with a non-empty diff."""
    built_on: frozenset[str] = frozenset()
    """Gerrit's current patch sets that are in the history of the local commit compared to them
    (``core/patchset_history.py``): the ones a ``newer`` commit has not **diverged** from."""
    files: dict[str, frozenset[str]] = field(default_factory=dict)
    """The files each present single-parent commit with a non-empty diff changes, from the same diff
    as its patch-id (a path git quotes is left out)."""


def read_code_facts(cwd: Path | str | None, shas: Iterable[str | None]) -> CodeFacts:
    """:class:`CodeFacts` for *shas*: three git calls however many there are, none for none."""
    wanted = sorted({norm_sha(s) for s in shas if s})
    if not wanted:
        return CodeFacts()
    listed = git("cat-file", "--batch-check=%(objectname) %(objecttype)", cwd=cwd, input="\n".join(wanted) + "\n")
    present = frozenset(line.split()[0] for line in listed.stdout.splitlines() if line.endswith(" commit"))
    if not present:
        return CodeFacts()
    # diff-tree shows nothing for a merge or a root commit: neither gets a patch-id.
    diffs = git("diff-tree", "-p", "--stdin", cwd=cwd, input="\n".join(sorted(present)) + "\n", check=False)
    if not diffs.stdout.strip():
        return CodeFacts(present)
    ids = git("patch-id", cwd=cwd, input=diffs.stdout, check=False).stdout
    pairs = (line.split() for line in ids.splitlines())
    return CodeFacts(present, {commit: pid for pid, commit, *_ in pairs}, files=_changed_files(diffs.stdout, present))


def _changed_files(patches: str, commits: frozenset[str]) -> dict[str, frozenset[str]]:
    """By commit, the paths of the ``diff --git a/P b/P`` headers ``git diff-tree --stdin -p`` printed for it."""
    files: dict[str, set[str]] = {}
    at: str | None = None
    for line in patches.splitlines():
        if line in commits:
            at = line
        elif at and line.startswith("diff --git a/"):
            both = line[len("diff --git ") :]
            path = both[2 : (len(both) - 1) // 2]  # no rename detection in diff-tree: a/P b/P, the same P
            if both == f"a/{path} b/{path}":
                files.setdefault(at, set()).add(path)
    return {commit: frozenset(paths) for commit, paths in files.items()}


def merged_equivalent(local_sha: str, change: ChangeView, facts: CodeFacts) -> bool | None:
    """
    v1 conservative equivalence: SHA match, else patch-id match when both sides are single-parent
    and the Gerrit ``current_revision`` exists locally. Merge commits and missing objects → ``None``.
    """
    cur = change.current_revision
    if cur is None:
        return None
    if norm_sha(local_sha) == norm_sha(cur):
        return True
    if norm_sha(cur) not in facts.present:
        return None
    pl = facts.patch_ids.get(norm_sha(local_sha))
    pg = facts.patch_ids.get(norm_sha(cur))
    if pl is None or pg is None:
        return None
    return pl == pg


class CodeChange(str, Enum):
    """How a commit ahead of Gerrit's current patch set differs from it: what decides whether its votes stay."""

    REBASED = "rebased"
    """Only rebased: the same patch-id (``git patch-id`` leaves out whitespace and the message), so
    Gerrit usually carries the votes over when it is pushed; the project's label rules decide."""
    CHANGED = "changed"
    """Its code changed: the votes are about the pushed patchset, and Gerrit may not keep them."""
    UNKNOWN = "unknown"
    """Cannot be told here: either side is a merge, a root or an empty commit, or Gerrit's patchset
    is not in this repository."""


def code_change(local_sha: str, change: ChangeView, facts: CodeFacts) -> CodeChange:
    """Whether *local_sha*, ahead of *change*'s current patch set, changed its code or is only rebased."""
    same = merged_equivalent(local_sha, change, facts)
    return CodeChange.UNKNOWN if same is None else CodeChange.REBASED if same else CodeChange.CHANGED


def files_dropped(local_sha: str, change: ChangeView, facts: CodeFacts) -> tuple[str, ...]:
    """The files *change*'s current patch set changes that *local_sha*, ahead of it, no longer does.

    Empty when that cannot be told: Gerrit's patch set is not here, or either side is a merge, a root
    or an empty commit.
    """
    pushed = facts.files.get(norm_sha(change.current_revision or ""))
    local = facts.files.get(norm_sha(local_sha))
    return tuple(sorted(pushed - local)) if pushed is not None and local is not None else ()


def norm_sha(sha: str) -> str:
    """Normalize commit SHA text for case-insensitive comparisons."""

    return sha.strip().lower()
