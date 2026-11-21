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
    return CodeFacts(present, {commit: pid for pid, commit, *_ in pairs})


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


def norm_sha(sha: str) -> str:
    """Normalize commit SHA text for case-insensitive comparisons."""

    return sha.strip().lower()
