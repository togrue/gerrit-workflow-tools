"""What the index and work tree hold of yours, sorted by what ``git stash`` makes of it.

Every ger command that refuses to move over your edits asks here, so changes to tracked files,
submodules staged at another commit and submodules a file replaced are told apart in one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gerrit_workflow_tools.core.git_run import git_out


_GITLINK, _ABSENT = "160000", "000000"


@dataclass(frozen=True)
class TrackedChanges:
    """What the index and work tree hold that a base commit does not, by what ``git stash`` makes of it.

    A submodule's own state (another commit checked out, edits inside it, its directory gone) is
    in none of them: switching, rebasing and ``git stash`` leave it alone, and lose nothing of it.
    """

    files: tuple[str, ...] = ()
    """Changes to tracked files, staged or not: what ``git stash`` saves."""
    staged_submodules: tuple[str, ...] = ()
    """Submodules staged at another commit than the base records, or staged for removal:
    ``git stash`` does not save them alone, and a rebase stops on them."""
    replaced_submodules: tuple[str, ...] = ()
    """Submodules a file or symlink replaced in the work tree, not staged: ``git stash`` does not
    save it, and a switch or rebase deletes it."""


def _raw_diff(cwd: Path | str | None, *args: str) -> list[tuple[str, str, str]]:
    """``(old mode, new mode, path)`` per record of ``git diff --raw`` with *args*, submodules included."""
    raw = git_out("diff", "--raw", "-z", "--no-renames", "--ignore-submodules=none", *args, cwd=cwd)
    fields = raw.split("\0")
    # -z: each record is ``:mode mode sha sha status`` NUL ``path`` NUL, the path as is (never C-quoted).
    records = []
    for meta, path in zip(fields[::2], fields[1::2], strict=False):
        old, new = meta.lstrip(":").split()[:2]
        records.append((old, new, path))
    return records


def tracked_changes(cwd: Path | str | None, base: str = "HEAD") -> TrackedChanges:
    """What the index (against *base*) and the work tree (against the index) hold that *base* does not.

    Every path changed against *base* is changed in one of the two. A gitlink kept a gitlink, or
    removed, is the submodule's own state in the work tree; in the index it is a staged
    submodule. A gitlink that became a file or symlink is a file change once staged (``git stash``
    saves it) and a replaced submodule while it is not.
    """
    files: set[str] = set()
    staged: list[str] = []
    replaced: list[str] = []
    for old, new, path in _raw_diff(cwd, "--cached", *(() if base == "HEAD" else (base,))):
        if _GITLINK in (old, new) and new in (_GITLINK, _ABSENT):
            staged.append(path)
        else:
            files.add(path)
    for old, new, path in _raw_diff(cwd):
        if old != _GITLINK:
            files.add(path)
        elif new not in (_GITLINK, _ABSENT):
            replaced.append(path)
    return TrackedChanges(tuple(sorted(files)), tuple(staged), tuple(replaced))
