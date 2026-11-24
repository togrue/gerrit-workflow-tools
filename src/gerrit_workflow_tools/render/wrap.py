"""Word wrap for terminal prose, and :func:`say`, which prints a ``next:`` or closing line wrapped so.

Breaks on spaces and tabs. A token with no whitespace is sliced into
``width``-sized pieces. The width is a character count of the text itself.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from typing import TextIO

from gerrit_workflow_tools.render.style import ANSI_DIM, color_text


# Stands in for a space inside a `command` while wrapping, so a command is never broken.
_KEPT_SPACE = "\ue000"
_COMMAND_RE = re.compile(r"`[^`]*`")


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


def wrap_hanging(text: str, width: int, *, indent: int) -> list[str]:
    """*text* (one line) wrapped to *width*, each line after the first indented by *indent* spaces.

    A `command` in backticks is kept on one line, so it can be copied whole.
    """
    kept = _COMMAND_RE.sub(lambda m: m.group(0).replace(" ", _KEPT_SPACE), text)
    first = _wrap_one(kept, width)[0]
    rest = kept[len(first) :].lstrip(" \t")
    lines = [first]
    if rest:
        lines += [" " * indent + line for line in _wrap_one(rest, max(width - indent, 1))]
    return [line.replace(_KEPT_SPACE, " ") for line in lines]


_NEXT = "next: "
_STEPS = " · "
_NOTE_INDENT = 2


def note_lines(line: str, width: int | None) -> list[str]:
    """A ``next:`` or closing line as ger prints it on a terminal *width* wide; as it is for ``None`` (piped).

    Broken between words, never inside a `command` in backticks nor inside one step of a
    `` · ``-separated list of them; what follows ``next: `` is indented under its text, any
    other line's by two spaces.
    """
    if not width or len(line) <= width:
        return [line]
    indent = len(_NEXT) if line.startswith(_NEXT) else _NOTE_INDENT
    if _STEPS in line:
        line = _STEPS.join(step.replace(" ", _KEPT_SPACE) for step in line.split(_STEPS))
        line = line.replace(_NEXT.replace(" ", _KEPT_SPACE), _NEXT, 1)
    return wrap_hanging(line, width, indent=indent)


def stream_columns(stream: TextIO) -> int | None:
    """The columns of the terminal *stream* writes to; ``None`` when it is piped or redirected."""
    if not getattr(stream, "isatty", lambda: False)():
        return None
    try:
        return os.get_terminal_size(stream.fileno()).columns
    except (OSError, ValueError, AttributeError):
        return shutil.get_terminal_size().columns


def say(line: str, *, file: TextIO | None = None, dim: bool = False) -> None:
    """Print a ``next:`` or closing line (stderr unless *file*): on a terminal wrapped by :func:`note_lines`,
    never mid-word; piped, one line as it is. Every such line goes through here, so all wrap alike."""
    stream = file if file is not None else sys.stderr
    for part in note_lines(line, stream_columns(stream)):
        print(color_text(part, ANSI_DIM) if dim else part, file=stream)


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
