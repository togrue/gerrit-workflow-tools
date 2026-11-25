"""A commit's diff for a reviewer at the terminal: git's colours, and each line's new-side number in
a gutter, the number ``ger comment PATH:LINE`` takes."""

from __future__ import annotations

from collections.abc import Sequence

from gerrit_workflow_tools.core.commit_diff import DiffLine
from gerrit_workflow_tools.render.style import ANSI_BOLD, ANSI_CYAN, ANSI_DIM, ANSI_GREEN, ANSI_RED, color_text


_COLORS = {"file": ANSI_BOLD, "hunk": ANSI_CYAN, "added": ANSI_GREEN, "removed": ANSI_RED, "note": ANSI_DIM}


def diff_lines(lines: Sequence[DiffLine]) -> list[str]:
    """Each diff line behind a gutter with its new-side line number; a removed line has none, as it
    cannot be commented on."""
    width = max((len(str(line.new)) for line in lines if line.new is not None), default=1)
    out: list[str] = []
    for line in lines:
        number = "" if line.new is None else str(line.new)
        gutter = color_text(f"{number:>{width}} │", ANSI_DIM)
        text = color_text(line.text, _COLORS[line.kind]) if line.kind in _COLORS else line.text
        out.append(text if line.kind == "file" else f"{gutter}{text}")
    return out
