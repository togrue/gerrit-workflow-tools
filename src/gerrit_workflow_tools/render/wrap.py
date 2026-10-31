"""Word wrap for terminal prose.

Breaks on spaces and tabs. A token with no whitespace is sliced into
``width``-sized pieces. The width is a character count of the text itself.
"""

from __future__ import annotations


def wrap_lines(text: str, width: int) -> list[str]:
    """Split *text* into lines of at most *width* characters.

    Existing newlines are preserved. Each line is broken on spaces and tabs.
    A token longer than *width* is sliced by characters.
    """
    if width < 1:
        raise ValueError(f"width must be >= 1, got {width}")
    if text == "":
        return [""]
    lines: list[str] = []
    for line in text.splitlines() or [""]:
        lines.extend(_wrap_one(line, width))
    return lines or [""]


def fit_header(text: str, width: int) -> str:
    """Fit a single-line header into *width*, keeping the tail.

    Paths have no useful word break. The filename and a trailing
    ``(resolved)`` marker sit at the end, so the front of the path is what drops.
    """
    if width < 1:
        return ""
    if len(text) <= width:
        return text
    if width == 1:
        return text[-1]
    return "…" + text[-(width - 1) :]


def _wrap_one(line: str, width: int) -> list[str]:
    if len(line) <= width:
        return [line]
    out: list[str] = []
    rest = line
    while rest:
        if len(rest) <= width:
            out.append(rest)
            break
        if rest[width].isspace():
            out.append(rest[:width].rstrip(" \t"))
            rest = rest[width:].lstrip(" \t")
            continue
        lead = len(rest) - len(rest.lstrip(" \t"))
        break_at = _last_break(rest[:width], start=lead)
        if break_at <= 0:
            out.append(rest[:width])
            rest = rest[width:]
            continue
        out.append(rest[:break_at])
        rest = rest[break_at:].lstrip(" \t")
    return out or [""]


def _last_break(window: str, *, start: int) -> int:
    """Index of the last whitespace run in *window* at or after *start*, or -1."""
    break_at = -1
    i = start
    while i < len(window):
        if window[i].isspace():
            break_at = i
            while i < len(window) and window[i].isspace():
                i += 1
            continue
        i += 1
    return break_at
