"""What one commit changed: its files against the first parent, and a file's text at a revision."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from gerrit_workflow_tools.core.git_run import UnknownRevisionError, commit_sha_or_none, git


EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
"""Git's empty tree: what a root commit is diffed against."""

_DIFF_FLAGS = ("-M", "-z", "--no-color", "--no-relative", "--no-ext-diff")


@dataclass(frozen=True)
class FileChange:
    """One file a commit touched."""

    path: str
    status: str
    """Git's status letter: ``A``, ``M``, ``D``, ``R`` (renamed), ``C`` (copied), ``T`` (type changed)."""
    old_path: str | None = None
    """Where a renamed or copied file came from."""
    insertions: int | None = None
    deletions: int | None = None
    """Line counts; both ``None`` for a binary file."""


@dataclass(frozen=True)
class CommitDiff:
    """The files one commit changed against its first parent."""

    commit: str
    base: str
    """The first parent, or :data:`EMPTY_TREE` for a root commit."""
    files: list[FileChange]


def _name_status(fields: list[str]) -> list[tuple[str, str, str | None]]:
    """``(status letter, path, old path)`` rows from ``--name-status -z`` fields."""
    rows: list[tuple[str, str, str | None]] = []
    i = 0
    while i < len(fields) and fields[i]:
        letter = fields[i][:1]
        if letter in "RC" and i + 2 < len(fields):
            rows.append((letter, fields[i + 2], fields[i + 1]))
            i += 3
        elif i + 1 < len(fields):
            rows.append((letter, fields[i + 1], None))
            i += 2
        else:
            break
    return rows


def _numstat(fields: list[str]) -> dict[str, tuple[int | None, int | None]]:
    """``path -> (insertions, deletions)`` from ``--numstat -z`` fields; a rename is keyed by its new path."""
    counts: dict[str, tuple[int | None, int | None]] = {}
    i = 0
    while i < len(fields) and fields[i]:
        added, _, rest = fields[i].partition("\t")
        deleted, _, path = rest.partition("\t")
        if not path and i + 2 < len(fields):  # rename: counts, then old and new path as own fields
            path = fields[i + 2]
            i += 2
        counts[path] = (int(added) if added.isdigit() else None, int(deleted) if deleted.isdigit() else None)
        i += 1
    return counts


def diff_between(cwd: Path | str | None, base: str, commit: str, paths: Sequence[str] = ()) -> list[FileChange]:
    """The files that differ from *base* to *commit*, renames detected; only *paths* when given."""
    limit = ("--", *paths) if paths else ()
    listed = git("diff", "--name-status", *_DIFF_FLAGS, base, commit, *limit, cwd=cwd).stdout.split("\0")
    counts = _numstat(git("diff", "--numstat", *_DIFF_FLAGS, base, commit, *limit, cwd=cwd).stdout.split("\0"))
    return [
        FileChange(path, letter, old_path, *counts.get(path, (None, None)))
        for letter, path, old_path in _name_status(listed)
    ]


def commit_diff(cwd: Path | str | None, rev: str) -> CommitDiff:
    """The files *rev* changed against its first parent, renames detected."""
    commit = commit_sha_or_none(cwd, rev)
    if commit is None:
        raise UnknownRevisionError(f"unknown revision {rev!r}")
    base = commit_sha_or_none(cwd, f"{commit}^") or EMPTY_TREE
    return CommitDiff(commit=commit, base=base, files=diff_between(cwd, base, commit))


def file_text(cwd: Path | str | None, rev: str, path: str) -> str | None:
    """Text of *path* at *rev* (a commit or tree); ``None`` when the file is not there."""
    shown = git("show", f"{rev}:{path}", cwd=cwd, check=False)
    return shown.stdout if shown.returncode == 0 else None
