"""Shared terminal styling helpers for CLI commands."""

from __future__ import annotations

import re
import sys
from typing import TextIO

ANSI_RESET = "\033[0m"
ANSI_DIM = "\033[2m"
ANSI_STRIKE = "\033[9m"
ANSI_GREEN = "\033[32m"
ANSI_RED = "\033[31m"
ANSI_LIGHT_GREEN = "\033[92m"
ANSI_YELLOW = "\033[33m"
ANSI_BOLD = "\033[1m"
ANSI_CYAN = "\033[36m"
ANSI_DIM_GRAY = "\033[2;37m"
ANSI_DIM_YELLOW = "\033[2;33m"

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")
_COLOR_ENABLED = False


def set_color_mode(enabled: bool) -> None:
    """Set global color mode for CLI output formatting."""
    global _COLOR_ENABLED
    _COLOR_ENABLED = bool(enabled)


def is_color_enabled() -> bool:
    """Return whether ANSI color styling is globally enabled."""
    return _COLOR_ENABLED


def init_color_mode(*, color: str = "auto", stream: TextIO | None = None) -> bool:
    """Initialize global color mode from ``--color`` and output TTY capability."""
    out = stream or sys.stdout
    tty = bool(getattr(out, "isatty", lambda: False)())
    if color == "always":
        enabled = True
    elif color == "never":
        enabled = False
    else:
        enabled = tty
    set_color_mode(enabled)
    return enabled


def color_text(text: str, code: str) -> str:
    """Colorize text using ANSI SGR when global color mode is enabled."""
    if not is_color_enabled():
        return text
    return f"{code}{text}{ANSI_RESET}"


# Abbreviated (and inline) commit SHAs use one color everywhere in the CLIs.
SHORT_SHA_SGR = ANSI_CYAN


def color_short_sha(text: str) -> str:
    """Colorize a displayed Git commit SHA for consistent status output (abbreviated or full)."""
    return color_text(text, SHORT_SHA_SGR)


def visible_len(text: str) -> int:
    """Length of terminal-visible characters (ANSI and strike combining chars ignored)."""
    plain = _ANSI_ESCAPE_RE.sub("", text)
    return len(plain.replace("\u0336", ""))
