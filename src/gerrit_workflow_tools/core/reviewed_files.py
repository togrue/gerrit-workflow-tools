"""Gerrit's per-file *reviewed* flag: which files of one patchset you marked as read.

The flag is yours and per patchset; Gerrit's web UI ticks the same files. ``ger review --seen``
and ``ger serve``'s ``/reviewed`` both read and set it here. Gerrit sets it one file per
request (it has no call for several), so marking N files costs N requests; nothing is resolved,
since the change and the patchset are given as numbers.
"""

from __future__ import annotations

from collections.abc import Sequence

from gerrit_workflow_tools.core.gerrit.rest import GerritRest


class ReviewedPathError(ValueError):
    """A path that is not a repository path (empty, an option, or with a NUL byte)."""


def check_reviewed_path(path: str) -> str:
    """*path* when it can name a file of a patchset; :class:`ReviewedPathError` otherwise."""
    if not path or path.startswith(("-", "/")) or "\0" in path:
        raise ReviewedPathError(f"not a repository path: {path!r}")
    return path


def reviewed_files(rest: GerritRest, change: int, patch_set: int) -> list[str]:
    """The paths of patchset *patch_set* of change *change* you marked reviewed, sorted: one request."""
    return sorted(rest.list_reviewed_files(str(change), patch_set))


def mark_reviewed(
    rest: GerritRest, change: int, patch_set: int, paths: Sequence[str], reviewed: bool = True
) -> list[str]:
    """Mark each of *paths* reviewed (or clear the mark), in order, each once: the paths set.

    Every path is checked before the first request, so a bad one changes nothing.
    """
    unique = list(dict.fromkeys(check_reviewed_path(path) for path in paths))
    for path in unique:
        rest.set_file_reviewed(str(change), patch_set, path, reviewed)
    return unique
