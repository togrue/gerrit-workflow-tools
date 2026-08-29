"""Shared terminal styling helpers for CLI commands."""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping


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

GERRIT_LINK_LABEL = "Open in gerrit"

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")
# OSC 8: ESC ] 8 ; params ; url ST  (ST is ESC \ or BEL)
_OSC8_RE = re.compile(r"\x1b\]8;[^;]*;[^\x1b\x07]*(?:\x1b\\|\x07)")
_OSC8_ST = "\x1b\\"

_HYPERLINK_TERM_PROGRAMS = frozenset(
    {
        "iTerm.app",
        "WezTerm",
        "ghostty",
        "vscode",
        "Hyper",
        "WarpTerminal",
        "Tabby",
    }
)
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSY = frozenset({"0", "false", "no", "off"})

_COLOR_ENABLED = False
_HYPERLINK_ENABLED = False


def set_color_mode(enabled: bool) -> None:
    """Set global color mode for CLI output formatting."""
    global _COLOR_ENABLED  # pylint: disable=global-statement
    _COLOR_ENABLED = bool(enabled)


def is_color_enabled() -> bool:
    """Return whether ANSI color styling is globally enabled."""
    return _COLOR_ENABLED


def init_color_mode(*, color: str = "auto") -> bool:
    """Initialize global color mode from ``--color`` and output TTY capability."""
    out = sys.stdout
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
    if not _COLOR_ENABLED:
        return text
    return f"{code}{text}{ANSI_RESET}"


# Abbreviated (and inline) commit SHAs use one color everywhere in the CLIs.
SHORT_SHA_SGR = ANSI_CYAN


def color_short_sha(text: str) -> str:
    """Colorize a displayed Git commit SHA for consistent status output (abbreviated or full)."""
    return color_text(text, SHORT_SHA_SGR)


def set_hyperlink_mode(enabled: bool) -> None:
    """Set global OSC 8 hyperlink mode for CLI output formatting."""
    global _HYPERLINK_ENABLED  # pylint: disable=global-statement
    _HYPERLINK_ENABLED = bool(enabled)


def is_hyperlink_enabled() -> bool:
    """Return whether OSC 8 hyperlinks are globally enabled."""
    return _HYPERLINK_ENABLED


def terminal_supports_hyperlinks(environ: Mapping[str, str] | None = None) -> bool:
    """Return whether *environ* looks like a terminal that understands OSC 8.

    Heuristics only — does not inspect TTY state. Prefer a false negative over
    emitting OSC sequences that dump as garbage.
    """
    env = os.environ if environ is None else environ
    term = env.get("TERM", "")
    if term == "dumb":
        return False
    vte = env.get("VTE_VERSION", "")
    return bool(
        env.get("WT_SESSION")
        or env.get("TERM_PROGRAM") in _HYPERLINK_TERM_PROGRAMS
        or (vte.isdigit() and int(vte) >= 5000)
        or env.get("KITTY_WINDOW_ID")
        or term == "xterm-kitty"
        or env.get("ALACRITTY_SOCKET")
        or term == "alacritty"
        or env.get("KONSOLE_VERSION")
        or "DOMTERM" in env
    )


def _env_flag(env: Mapping[str, str], name: str) -> str:
    return env.get(name, "").strip().lower()


def _stdout_is_tty() -> bool:
    return bool(getattr(sys.stdout, "isatty", lambda: False)())


def init_hyperlink_mode(*, hyperlinks: str = "auto") -> bool:
    """Initialize OSC 8 hyperlink mode from ``--hyperlinks`` and terminal capability."""
    env = os.environ
    if hyperlinks == "always":
        enabled = True
    elif hyperlinks == "never":
        enabled = False
    else:
        no_hyperlinks = _env_flag(env, "NO_HYPERLINKS")
        force = _env_flag(env, "FORCE_HYPERLINK")
        if (no_hyperlinks and no_hyperlinks not in _FALSY) or force in _FALSY:
            enabled = False
        elif force in _TRUTHY:
            enabled = _stdout_is_tty()
        else:
            enabled = _stdout_is_tty() and terminal_supports_hyperlinks(env)
    set_hyperlink_mode(enabled)
    return enabled


def format_link(url: str, *, label: str = GERRIT_LINK_LABEL) -> str:
    """Return an OSC 8 hyperlink when enabled, otherwise the raw URL."""
    if not url:
        return url
    if not is_hyperlink_enabled() or "\x1b" in url or "\x07" in url:
        return url
    return f"\x1b]8;;{url}{_OSC8_ST}{label}\x1b]8;;{_OSC8_ST}"


def strip_ansi(text: str) -> str:
    """Remove SGR and OSC 8 sequences; combining strikethrough chars are kept."""
    plain = _OSC8_RE.sub("", text)
    return _ANSI_ESCAPE_RE.sub("", plain)


def visible_len(text: str) -> int:
    """Length of terminal-visible characters (ANSI and strike combining chars ignored)."""
    return len(strip_ansi(text).replace("\u0336", ""))
