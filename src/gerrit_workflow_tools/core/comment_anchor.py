"""Where a new comment sits: the LOCATION grammar, its check against a commit, the anchor.

``PATH``, ``PATH:LINE``, ``PATH:START-END`` and ``PATH:L.C-L.C``. Lines and columns count
from 1 and columns are inclusive, as editors show them; :class:`CommentRange` holds Gerrit's
0-based, end-exclusive characters. Spec: docu/spec/commands/comment.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any

from gerrit_workflow_tools.core.gerrit.stack_context import ChangeResolutionError
from gerrit_workflow_tools.core.git_run import git
from gerrit_workflow_tools.core.git_state import repo_toplevel


_POSITION = r"(\d+)(?:\.(\d+))?"
_LOCATION_SUFFIX = re.compile(rf"^{_POSITION}(?:-{_POSITION})?$")


class LocationError(ValueError):
    """A LOCATION that is malformed, or does not fit the file it names (a usage error)."""


@dataclass(frozen=True)
class CommentRange:
    """Gerrit's ``CommentRange``: 1-based lines, 0-based end-exclusive characters."""

    start_line: int
    start_character: int
    end_line: int
    end_character: int

    def as_gerrit(self) -> dict[str, int]:
        """The ``range`` object of a Gerrit ``CommentInput``."""
        return {
            "start_line": self.start_line,
            "start_character": self.start_character,
            "end_line": self.end_line,
            "end_character": self.end_character,
        }


@dataclass(frozen=True)
class CommentLocation:
    """A file, a line, or a range in one file of a commit."""

    path: str
    line: int | None = None
    """The line Gerrit files the comment under: the range's end line for a range."""
    range: CommentRange | None = None
    end_column_open: bool = False
    """The range ends at the end of its last line, which only the file's text can say."""

    @property
    def start_line(self) -> int | None:
        """First line: the range's, else ``line``."""
        return self.range.start_line if self.range is not None else self.line

    def as_gerrit(self) -> dict[str, Any]:
        """``path``, ``line`` and ``range`` of a Gerrit ``CommentInput``."""
        fields: dict[str, Any] = {"path": self.path}
        if self.line is not None:
            fields["line"] = self.line
        if self.range is not None:
            fields["range"] = self.range.as_gerrit()
        return fields

    def describe(self) -> str:
        """``path``, ``path:line`` or ``path:start-end``, as ``ger show`` prints a chain."""
        start = self.start_line
        if self.line is None:
            return self.path
        if start is not None and start != self.line:
            return f"{self.path}:{start}-{self.line}"
        return f"{self.path}:{self.line}"


def parse_location(text: str) -> CommentLocation:
    """Parse a LOCATION. Raises :class:`LocationError` for a malformed or backwards one."""
    raw = text.strip()
    path, sep, suffix = raw.rpartition(":")
    match = _LOCATION_SUFFIX.match(suffix) if sep else None
    if match is None:
        return CommentLocation(path=_clean_path(raw))
    path = _clean_path(path)
    start_line, start_col, end_line, end_col = (int(g) if g else None for g in match.groups())
    assert start_line is not None
    if end_line is None:
        if start_col is not None:
            raise LocationError(f"{raw!r}: a column needs a range (LINE.COL-LINE.COL)")
        return CommentLocation(path=path, line=_positive(start_line, raw))
    for value in (start_line, start_col, end_line, end_col):
        if value is not None:
            _positive(value, raw)
    if end_line < start_line or (end_line == start_line and end_col is not None and end_col < (start_col or 1)):
        raise LocationError(f"{raw!r}: the range ends before it starts")
    comment_range = CommentRange(
        start_line=start_line,
        start_character=(start_col or 1) - 1,
        end_line=end_line,
        end_character=end_col if end_col is not None else 0,
    )
    return CommentLocation(path=path, line=end_line, range=comment_range, end_column_open=end_col is None)


def _positive(value: int, raw: str) -> int:
    if value < 1:
        raise LocationError(f"{raw!r}: lines and columns count from 1")
    return value


def _clean_path(path: str) -> str:
    cleaned = PurePosixPath(path.replace("\\", "/")).as_posix().removeprefix("./")
    if not cleaned or cleaned in (".", "/") or cleaned.startswith("/") or ".." in PurePosixPath(cleaned).parts:
        raise LocationError(f"{path!r}: give a path inside the repository")
    return cleaned


def repository_path(cwd: Path | str, location: CommentLocation) -> CommentLocation:
    """*location* with its path relative to the repository top level.

    A path that exists relative to *cwd* (an editor's terminal in a subdirectory) is
    rewritten to its top-level form; anything else is taken as top-level relative.
    """
    top = repo_toplevel(cwd)
    if top is None:
        return location
    candidate = (Path(cwd) / location.path).resolve()
    try:
        relative = candidate.relative_to(top.resolve())
    except ValueError:
        return location
    if not candidate.exists() or relative.as_posix() == location.path:
        return location
    return replace(location, path=relative.as_posix())


def file_lines(cwd: Path | str, sha: str, path: str) -> list[str] | None:
    """The lines of *path* in commit *sha*, or ``None`` when the commit has no such file."""
    shown = git("show", f"{sha}:{path}", cwd=cwd, check=False)
    if shown.returncode != 0:
        return None
    return shown.stdout.splitlines()


def blob_of(cwd: Path | str, sha: str, path: str) -> str | None:
    """The blob SHA of *path* in commit *sha*, or ``None`` when it has no such file."""
    shown = git("rev-parse", "-q", "--verify", f"{sha}:{path}", cwd=cwd, check=False)
    return shown.stdout.strip() or None if shown.returncode == 0 else None


def fit_to_file(location: CommentLocation, lines: list[str] | None, *, commit: str) -> CommentLocation:
    """Check *location* against the file's *lines* in *commit*, and close an open-ended range.

    Raises :class:`ChangeResolutionError` when the file is not in the commit, and
    :class:`LocationError` for a line or column past its end. With *lines* unknown (the
    commit is not local), an open-ended range ends at the start of the next line instead.
    """
    if lines is None:
        raise ChangeResolutionError(f"{location.path} is not in commit {commit[:12]}")
    count = len(lines)
    for line in (location.start_line, location.line):
        if line is not None and line > count:
            raise LocationError(f"{location.describe()}: {location.path} has {count} line(s) in {commit[:12]}")
    rng = location.range
    if rng is None:
        return location
    if rng.start_character > len(lines[rng.start_line - 1]):
        raise LocationError(
            f"{location.describe()}: line {rng.start_line} is shorter than column {rng.start_character + 1}"
        )
    if location.end_column_open:
        rng = replace(rng, end_character=len(lines[rng.end_line - 1]))
    elif rng.end_character > len(lines[rng.end_line - 1]):
        raise LocationError(f"{location.describe()}: line {rng.end_line} is shorter than column {rng.end_character}")
    return replace(location, range=rng, end_column_open=False)


def close_unchecked(location: CommentLocation) -> CommentLocation:
    """An open-ended range with no file text to measure: end it at the start of the next line."""
    rng = location.range
    if rng is None or not location.end_column_open:
        return location
    return replace(location, range=replace(rng, end_line=rng.end_line + 1, end_character=0), end_column_open=False)


def anchored_text(location: CommentLocation, lines: list[str]) -> str:
    """The text *location* covers in *lines*: its range, its line, or nothing for a file comment."""
    rng = location.range
    if rng is None:
        return lines[location.line - 1] if location.line is not None else ""
    covered = lines[rng.start_line - 1 : rng.end_line]
    if not covered:
        return ""
    if len(covered) == 1:
        return covered[0][rng.start_character : rng.end_character]
    return "\n".join([covered[0][rng.start_character :], *covered[1:-1], covered[-1][: rng.end_character]])
