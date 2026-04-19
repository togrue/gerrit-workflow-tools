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


def init_color_mode(*, no_color: bool, stream: TextIO | None = None) -> bool:
    """Initialize global color mode from ``--no-color`` and output TTY capability."""
    out = stream or sys.stdout
    tty = bool(getattr(out, "isatty", lambda: False)())
    enabled = (not no_color) and tty
    set_color_mode(enabled)
    return enabled


def color_text(text: str, code: str, *, enabled: bool | None = None) -> str:
    """Colorize text using ANSI SGR, honoring global mode by default."""
    use_color = is_color_enabled() if enabled is None else bool(enabled)
    if not use_color:
        return text
    return f"{code}{text}{ANSI_RESET}"


def visible_len(text: str) -> int:
    """Length of terminal-visible characters (ANSI and strike combining chars ignored)."""
    plain = _ANSI_ESCAPE_RE.sub("", text)
    return len(plain.replace("\u0336", ""))
