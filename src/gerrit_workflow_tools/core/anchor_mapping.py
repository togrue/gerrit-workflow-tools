"""Re-anchoring: where a local comment's anchor lands on another version of its file.

A pure function of (anchor blob, range) and the shown commit (ADR-0011): always one diff
from the anchored blob straight to the shown one, never chained through earlier
mappings, and nothing written back. Lines outside the diff's hunks shift by the hunks
before them; a range touching a hunk is **outdated** and sits just before that hunk.
Spec: docu/spec/commands/comment.md § Re-anchoring.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path

from gerrit_workflow_tools.core.comment_anchor import Anchor, CommentLocation, blob_of
from gerrit_workflow_tools.core.gerrit_change_status import ContextLine
from gerrit_workflow_tools.core.git_run import git


_EMPTY_BLOB = "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
_HUNK_HEADER = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? \+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)
_CACHE_LIMIT = 4096
_HUNKS: dict[tuple[str, str], tuple[Hunk, ...] | None] = {}
"""Hunks by (anchored blob, shown blob). Both are content-addressed, so an entry never goes stale."""


@dataclass(frozen=True)
class AnchorPlacement:
    """Where a local comment's anchor lands on the version of the file it is shown on."""

    shown: CommentLocation
    """Where the comment is shown: the anchor mapped onto the shown file."""
    anchored: CommentLocation
    """Where it was written."""
    text: str
    """The text it was written on."""
    moved: bool = False
    """Shown on another version of the file, or at another path, than it was written on."""
    outdated: bool = False
    """Its anchored lines changed or were deleted (or the file is gone): shown near where they were."""

    @classmethod
    def at_anchor(cls, anchor: Anchor) -> AnchorPlacement:
        """Shown on the very version it was written on."""
        return cls(shown=anchor.location, anchored=anchor.location, text=anchor.text)

    def moved_to(self, shown: CommentLocation) -> AnchorPlacement:
        """Cleanly mapped to *shown* on another version of the file."""
        return replace(self, shown=shown, moved=True)

    def outdated_at(self, shown: CommentLocation) -> AnchorPlacement:
        """Its lines are gone; shown at *shown*, next to where they were."""
        return replace(self, shown=shown, moved=True, outdated=True)

    @property
    def context_lines(self) -> tuple[ContextLine, ...]:
        """The anchored text as numbered lines, as it was: what an outdated comment shows beside it."""
        first = self.anchored.start_line or 1
        return tuple(ContextLine(first + i, text) for i, text in enumerate(self.text.splitlines()))


@dataclass(frozen=True)
class Hunk:
    """One ``-U0`` hunk: *old_count* lines from *old_start* became *new_count* from *new_start*.

    A count of 0 is a pure insertion (after old line *old_start*) or a pure deletion
    (after new line *new_start*), as ``git diff`` writes them.
    """

    old_start: int
    old_count: int
    new_start: int
    new_count: int


@dataclass(frozen=True)
class MappedLines:
    """Lines *start*..*end* of the anchored blob, as they sit in the shown one."""

    start: int
    end: int
    outdated: bool


def map_lines(hunks: tuple[Hunk, ...], start: int, end: int) -> MappedLines:
    """Map anchored lines *start*..*end* (1-based, inclusive) through *hunks*.

    Clean: each end shifts by the hunks above it (an insertion inside the range widens
    it). Outdated, when a hunk changes or deletes one of the lines: the line just before
    that hunk in the new version, or line 1 when the hunk starts the file.
    """
    for hunk in hunks:
        if hunk.old_count and hunk.old_start <= end and hunk.old_start + hunk.old_count - 1 >= start:
            before = hunk.old_start - 1 + _shift(hunks, hunk.old_start)
            return MappedLines(start=max(before, 1), end=max(before, 1), outdated=True)
    return MappedLines(start=start + _shift(hunks, start), end=end + _shift(hunks, end), outdated=False)


def _shift(hunks: tuple[Hunk, ...], line: int) -> int:
    """How far anchored *line* moves: the line-count change of every hunk above it."""
    return sum(hunk.new_count - hunk.old_count for hunk in hunks if _above(hunk, line))


def _above(hunk: Hunk, line: int) -> bool:
    """*hunk* ends above anchored *line*, so it shifts it."""
    if hunk.old_count == 0:
        return hunk.old_start < line
    return hunk.old_start + hunk.old_count - 1 < line


def blob_hunks(cwd: Path | str, old: str, new: str) -> tuple[Hunk, ...] | None:
    """The ``-U0`` hunks from blob *old* to blob *new*; ``None`` when git cannot diff them by line."""
    key = (old, new)
    if key not in _HUNKS:
        if len(_HUNKS) >= _CACHE_LIMIT:
            _HUNKS.clear()
        _HUNKS[key] = _diff_blobs(cwd, old, new)
    return _HUNKS[key]


def _diff_blobs(cwd: Path | str, old: str, new: str) -> tuple[Hunk, ...] | None:
    if old == new:
        return ()
    shown = git("diff", "-U0", "--no-color", "--no-ext-diff", "--no-textconv", old, new, cwd=cwd, check=False)
    if shown.returncode != 0:
        return None
    hunks = []
    for line in shown.stdout.splitlines():
        match = _HUNK_HEADER.match(line)
        if match:
            counts = {name: int(value) if value is not None else 1 for name, value in match.groupdict().items()}
            hunks.append(Hunk(**counts))
    if not hunks and "Binary files" in shown.stdout:
        return None
    return tuple(hunks)


def shown_path(cwd: Path | str, anchor: Anchor, commit: str) -> str | None:
    """Where the anchored file is in *commit*: its own path, else where it was renamed to.

    A rename is found by ``git diff -M`` from the anchor's commit when that commit is
    still here, else by a file in *commit* whose blob is the anchored one. ``None`` when
    the file is gone.
    """
    path = anchor.location.path
    if blob_of(cwd, commit, path) is not None:
        return path
    return _renamed_to(cwd, anchor.commit, commit, path) or _path_of_blob(cwd, commit, anchor.blob)


def _renamed_to(cwd: Path | str, old_commit: str, commit: str, path: str) -> str | None:
    if git("cat-file", "-e", f"{old_commit}^{{commit}}", cwd=cwd, check=False).returncode != 0:
        return None
    listed = git("diff", "-M", "--name-status", "-z", "--no-color", old_commit, commit, cwd=cwd, check=False)
    fields = listed.stdout.split("\0") if listed.returncode == 0 else []
    i = 0
    while i < len(fields) and fields[i]:
        status = fields[i]
        if status[:1] in "RC":
            if fields[i + 1] == path and status[:1] == "R":
                return fields[i + 2]
            i += 3
        else:
            i += 2
    return None


def _path_of_blob(cwd: Path | str, commit: str, blob: str | None) -> str | None:
    if blob is None:
        return None
    listed = git("ls-tree", "-r", "-z", commit, cwd=cwd, check=False)
    for entry in listed.stdout.split("\0") if listed.returncode == 0 else []:
        meta, _, name = entry.partition("\t")
        if meta.split(" ")[-1:] == [blob]:
            return name
    return None


def place_anchor(cwd: Path | str, anchor: Anchor, commit: str | None) -> AnchorPlacement:
    """Where *anchor* lands in *commit*'s version of its file (the anchor itself without a commit)."""
    location = anchor.location
    placement = AnchorPlacement.at_anchor(anchor)
    if commit is None or anchor.blob is None:
        return placement
    path = shown_path(cwd, anchor, commit)
    target = blob_of(cwd, commit, path) if path is not None else None
    if path is None or target is None:
        return placement.outdated_at(CommentLocation(path=location.path))
    if target == anchor.blob:
        return placement if path == location.path else placement.moved_to(replace(location, path=path))
    if location.line is None:
        return placement.moved_to(replace(location, path=path))
    hunks = blob_hunks(cwd, anchor.blob, target)
    if hunks is None or target == _EMPTY_BLOB:
        return placement.outdated_at(CommentLocation(path=path))
    start = location.start_line if location.start_line is not None else location.line
    mapped = map_lines(hunks, start, location.line)
    if mapped.outdated:
        return placement.outdated_at(CommentLocation(path=path, line=mapped.end))
    rng = location.range
    shown_range = replace(rng, start_line=mapped.start, end_line=mapped.end) if rng is not None else None
    return placement.moved_to(replace(location, path=path, line=mapped.end, range=shown_range))
