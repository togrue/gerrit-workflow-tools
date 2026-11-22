"""A CI log as clean lines: what every finder reads, and the shapes finders share.

Kept apart from the finders so each finder module depends on this one, not on its siblings.

Line prefixes a CI system adds in front of a tool's own output are removed here, once, so
no finder has to know them: ANSI colour, Jenkins timestamps, the ``[branch]`` tag of a
parallel pipeline stage, and Docker BuildKit's ``#12 3.4`` step prefix. A build tool's own
level tag (Maven's ``[ERROR]``, sbt's ``[error]``) stays: it is the only severity those
lines carry, and finders match it through :data:`LEVEL`.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

from gerrit_workflow_tools.core.ci_content import CiFinding, Severity


Finder = Callable[[Sequence[str], set[int]], list[CiFinding]]
"""``(lines, claimed) -> findings``; a finder adds the indices it used to *claimed*."""

PATH = r"(?:[A-Za-z]:)?[^\s:]+"
"""Regex fragment for a file path in a diagnostic: no spaces, an optional drive letter."""

LEVEL = r"\[(?P<level>ERROR|WARNING|WARN|error|warn)\]"
"""Regex fragment for the level tag Maven and sbt put in front of every line."""

DETAIL_LINES = 8
"""Lines of excerpt a finding's ``detail`` keeps."""

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_TIMESTAMP = re.compile(r"^\[?(?:\d{4}-\d\d-\d\d[T ])?\d\d:\d\d:\d\d(?:[.,]\d+)?Z?\]?\s+")
_BUILDKIT = re.compile(r"^#\d+ \d+\.\d+ ")
_PIPELINE_BRANCH = re.compile(r"^\[Pipeline\] \{ \(Branch: (?P<name>.+)\)$")


def level_severity(level: str | None) -> Severity:
    """The severity a level tag or a tool's own word stands for; anything but a warning is an error."""
    return "warning" if (level or "").lower() in ("warning", "warn") else "error"


def clean_log_lines(text: str) -> list[str]:
    """Log lines without ANSI escapes, carriage returns, or the prefixes a CI system adds."""
    lines = [_TIMESTAMP.sub("", _ANSI.sub("", line.rstrip("\r"))) for line in text.splitlines()]
    branches = {m.group("name") for line in lines if (m := _PIPELINE_BRANCH.match(line))}
    if branches:
        tags = tuple(f"[{name}] " for name in branches)
        # A tagged line carries its timestamp after the tag when both plugins are on.
        lines = [_TIMESTAMP.sub("", line[len(tag) :]) if (tag := _tag_of(line, tags)) else line for line in lines]
    return [_BUILDKIT.sub("", line) for line in lines]


def _tag_of(line: str, tags: tuple[str, ...]) -> str | None:
    return next((tag for tag in tags if line.startswith(tag)), None)


# --- workspace roots ---------------------------------------------------------------

_WORKSPACE = (
    re.compile(r"^Building (?:remotely on .+ )?in workspace (?P<root>.+)$"),
    re.compile(r"^Running on .+ in (?P<root>(?:/|[A-Za-z]:[\\/]).+)$"),
)


def normalize_log_path(path: str) -> str:
    """*path* with forward slashes, and without a ``\\\\?\\`` or ``./`` lead."""
    path = path.removeprefix("\\\\?\\").replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path


def workspace_roots(lines: Sequence[str]) -> tuple[str, ...]:
    """The directories the log says the build ran in (Jenkins' workspace lines), longest first."""
    roots = {
        normalize_log_path(m.group("root").strip()).rstrip("/")
        for line in lines
        for rx in _WORKSPACE
        if (m := rx.match(line))
    }
    return tuple(sorted((root for root in roots if root), key=len, reverse=True))


def workspace_relative(path: str, roots: Sequence[str]) -> str:
    """*path* normalized, and relative to the workspace root it lies under, if any."""
    path = normalize_log_path(path)
    for root in roots:
        head = path[: len(root) + 1]
        # A drive-letter root is a Windows path: its case is not significant.
        same = head.lower() == f"{root}/".lower() if root[1:2] == ":" else head == f"{root}/"
        if same and len(path) > len(head):
            return path[len(head) :]
    return path
