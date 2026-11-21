"""Gerrit fix suggestions written out below a comment: one shape for every renderer.

``ger show`` boxes them in the terminal, ``ger show --ai`` quotes them in Markdown, and
``ger serve`` sends a comment's whole text both ways, so a viewer shows them without
rendering them itself.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from gerrit_workflow_tools.core.suggested_edits import SuggestedEdit


FixParts = list[tuple[str, list[str]]]


def shown_fixes(edits: tuple[SuggestedEdit, ...], *, where: Callable[[str], str] = str) -> list[tuple[str, FixParts]]:
    """The Gerrit fix suggestions of *edits*, as each renderer writes them out below a comment.

    One ``(description, replacements)`` per fix; each replacement is what it does, its
    location passed through *where*, and the lines it writes (none for a delete). A
    ``suggestion`` block of the comment's text is left out: its message already shows it.
    """
    shown: list[tuple[str, FixParts]] = []
    for edit in edits:
        if edit.in_message:
            continue
        parts: FixParts = []
        for rep in edit.replacements:
            location = where(rep.describe())
            if not rep.replacement:
                parts.append((f"{location}: delete", []))
            else:
                parts.append((f"insert at {location}:" if rep.inserts else f"{location} becomes:", rep.lines()))
        shown.append((edit.description, parts))
    return shown


def code_fence(lines: list[str]) -> str:
    """A Markdown fence longer than any run of backticks in *lines*, so none of them closes it."""
    longest = max((len(run) for ln in lines for run in re.findall(r"`+", ln)), default=0)
    return "`" * max(3, longest + 1)


def fixes_markdown(edits: tuple[SuggestedEdit, ...]) -> list[list[str]]:
    """The fix suggestions of *edits* in Markdown, one list of lines per fix, each replacement fenced."""
    fixes: list[list[str]] = []
    for description, parts in shown_fixes(edits, where=lambda location: f"`{location}`"):
        lines = [f"**Suggested fix:** {description}".rstrip()]
        for what, code in parts:
            lines.append(what)
            if code:
                fence = code_fence(code)
                lines.extend([fence, *code, fence])
        fixes.append(lines)
    return fixes
