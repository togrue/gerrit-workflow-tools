"""Whether the CLI can ask: is stdin a terminal, and can a prompt_toolkit prompt start."""

from __future__ import annotations

import sys
from typing import TextIO


def _is_windows_console(stream: TextIO) -> bool:
    """Ask Windows itself: ``isatty`` is also true for ``NUL``, which ``/dev/null`` maps to.

    Elsewhere ``isatty`` is trusted, so the answer is always yes. The ``sys.platform`` guard
    (not ``os.name``) is what lets mypy skip the Windows-only APIs below on other platforms.
    """
    if sys.platform != "win32":
        return True
    import ctypes
    import msvcrt

    try:
        handle = msvcrt.get_osfhandle(stream.fileno())
    except (OSError, ValueError, AttributeError):
        return True
    return bool(ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(ctypes.c_ulong())))


def stdin_is_terminal() -> bool:
    """True when a prompt on stdin can be answered."""
    return sys.stdin.isatty() and (sys.platform != "win32" or _is_windows_console(sys.stdin))


def prompt_toolkit_can_run() -> bool:
    """True when a prompt_toolkit prompt can start: on Windows it also needs stdout to be a console."""
    return stdin_is_terminal() and (sys.platform != "win32" or _is_windows_console(sys.stdout))
