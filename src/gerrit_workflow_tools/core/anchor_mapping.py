"""Re-anchoring: where a local comment's anchor lands on another version of its file.

The same line mapping places a **CI finding** of the last pushed patchset on the local
commit (:func:`map_commit_lines`).

A pure function of (anchor blob, range) and the shown commit (ADR-0011): always one diff
from the anchored blob straight to the shown one, never chained through earlier
mappings, and nothing written back. Lines outside the diff's hunks shift by the hunks
before them; a range touching a hunk is **outdated** and sits just before that hunk.
Spec: docu/spec/commands/comment.md § Re-anchoring. :class:`WorktreeMap` maps the same way
onto the working files: where a Gerrit comment's lines sit in the file an editor shows.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from gerrit_workflow_tools.core.comment_anchor import Anchor, CommentLocation, CommentRange, blob_of
from gerrit_workflow_tools.core.comment_chain import ContextLine
from gerrit_workflow_tools.core.git_run import git
from gerrit_workflow_tools.core.git_state import repo_toplevel


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


class _DiffFailed(Exception):
    """git could not diff the blobs (one is missing, say): not a fact about their content."""


def tree_blobs(cwd: Path | str, commit: str, paths: Sequence[str]) -> dict[str, str] | None:
    """The blob of each of *paths* in *commit*, one ``git ls-tree``; ``None`` when the commit is not here.

    A path the commit has no file at is left out.
    """
    if not paths:
        return {}
    listed = git("ls-tree", "-z", "--full-tree", commit, "--", *paths, cwd=cwd, check=False)
    if listed.returncode != 0:
        return None
    found: dict[str, str] = {}
    for entry in listed.stdout.split("\0"):
        meta, _, name = entry.partition("\t")
        fields = meta.split(" ")
        if name and len(fields) == 3 and fields[1] == "blob":
            found[name] = fields[2]
    return found


def map_commit_lines(
    cwd: Path | str, old_commit: str, new_commit: str, wanted: Iterable[tuple[str, int]]
) -> dict[tuple[str, int], MappedLines] | None:
    """Where each ``(path, line)`` of *old_commit* sits in *new_commit*, as :func:`map_lines` maps it.

    One blob diff per file that differs. ``None`` when either commit is not here; a pair whose file
    is missing from either commit, or diffs as binary, is left out.
    """
    pairs = set(wanted)
    paths = sorted({path for path, _line in pairs})
    old, new = tree_blobs(cwd, old_commit, paths), tree_blobs(cwd, new_commit, paths)
    if old is None or new is None:
        return None
    mapped: dict[tuple[str, int], MappedLines] = {}
    for path, line in pairs:
        before, after = old.get(path), new.get(path)
        hunks = blob_hunks(cwd, before, after) if before and after else None
        if hunks is not None:
            mapped[(path, line)] = map_lines(hunks, line, line)
    return mapped


def blob_hunks(cwd: Path | str, old: str, new: str) -> tuple[Hunk, ...] | None:
    """The ``-U0`` hunks from blob *old* to blob *new*; ``None`` when they cannot be diffed by line.

    Only a diff git produced is cached: it is a fact about two content-addressed blobs.
    A failed diff is not, so it is retried next time.
    """
    key = (old, new)
    if key not in _HUNKS:
        try:
            hunks = _diff_blobs(cwd, old, new)
        except _DiffFailed:
            return None
        if len(_HUNKS) >= _CACHE_LIMIT:
            _HUNKS.clear()
        _HUNKS[key] = hunks
    return _HUNKS[key]


def _diff_blobs(cwd: Path | str, old: str, new: str) -> tuple[Hunk, ...] | None:
    """Hunks of the diff; ``None`` for a binary one. Raises :class:`_DiffFailed`."""
    if old == new:
        return ()
    # Pinned against user config: diff.interHunkContext would merge nearby hunks, and
    # an untouched line between two edits would then look changed.
    flags = ("-U0", "--inter-hunk-context=0", "--no-color", "--no-ext-diff", "--no-textconv")
    shown = git("diff", *flags, old, new, cwd=cwd, check=False)
    if shown.returncode != 0:
        raise _DiffFailed(shown.stderr.strip())
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
    flags = ("-M", "--name-status", "-z", "--no-color", "--no-relative")
    listed = git("diff", *flags, old_commit, commit, cwd=cwd, check=False)
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
    # --full-tree: from a subdirectory, ls-tree would list only that subtree, relative to it.
    listed = git("ls-tree", "-r", "-z", "--full-tree", commit, cwd=cwd, check=False)
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


@dataclass(frozen=True)
class WorktreePlace:
    """Where lines of a commented version of a file sit in the working file, as :func:`map_lines` maps them."""

    start_line: int
    line: int
    outdated: bool
    """They changed or are gone there: *line* is the one just before where they were."""


class WorktreeMap:
    """Lines of commits' files mapped onto the working files, for one answer.

    Each (commit, path) is diffed once, the commit's blob against the working file as git would
    store it (``git hash-object -w``, its filters applied), so the two diff as any two blobs do.
    ``None`` where it cannot be told: the commit or its file is not here, the working file is
    gone, or it is binary.
    """

    def __init__(self, cwd: Path | str) -> None:
        self._cwd = cwd
        self._top = repo_toplevel(cwd)
        self._hunks: dict[tuple[str, str], tuple[Hunk, ...] | None] = {}
        self._texts: dict[tuple[str, str], str | None] = {}

    def _diff(self, commit: str, path: str) -> tuple[Hunk, ...] | None:
        key = (commit, path)
        if key not in self._hunks:
            self._hunks[key] = self._hunks_of(commit, path)
        return self._hunks[key]

    def _hunks_of(self, commit: str, path: str) -> tuple[Hunk, ...] | None:
        old = blob_of(self._cwd, commit, path)
        if old is None or self._top is None or not (self._top / path).is_file():
            return None
        new = git("hash-object", "-w", "--", path, cwd=self._top, check=False)
        return blob_hunks(self._cwd, old, new.stdout.strip()) if new.returncode == 0 else None

    def place(self, commit: str | None, path: str, start: int, end: int) -> WorktreePlace | None:
        """Where lines *start*..*end* of *path* in *commit* are in the working file."""
        hunks = self._diff(commit, path) if commit else None
        if hunks is None:
            return None
        mapped = map_lines(hunks, start, end)
        return WorktreePlace(start_line=mapped.start, line=mapped.end, outdated=mapped.outdated)

    def range(self, commit: str | None, path: str, rng: CommentRange) -> CommentRange | None:
        """*rng* of *path* in *commit*, in the working file, while the lines it covers are there unchanged."""
        last = rng.end_line - 1 if rng.end_character == 0 and rng.end_line > rng.start_line else rng.end_line
        placed = self.place(commit, path, rng.start_line, last)
        if placed is None or placed.outdated or placed.line - placed.start_line != last - rng.start_line:
            return None
        shift = placed.start_line - rng.start_line
        return replace(rng, start_line=rng.start_line + shift, end_line=rng.end_line + shift)

    def text(self, commit: str | None, path: str, rng: CommentRange) -> str | None:
        """What *rng* covers in *path* of *commit*: the text a suggested edit replaces."""
        key = (commit or "", path)
        if key not in self._texts:
            shown = git("cat-file", "blob", f"{commit}:{path}", cwd=self._cwd, check=False) if commit else None
            self._texts[key] = shown.stdout if shown is not None and shown.returncode == 0 else None
        content = self._texts[key]
        if content is None:
            return None
        lines = content.splitlines(keepends=True)

        def offset(line: int, character: int) -> int:
            return sum(len(text) for text in lines[: line - 1]) + character

        return content[offset(rng.start_line, rng.start_character) : offset(rng.end_line, rng.end_character)]
