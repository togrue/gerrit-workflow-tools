"""A one-line meter on a terminal for bytes that are still arriving (CI logs, mostly).

Downloads run on several threads at once; the meter adds them up and redraws one line in
place, at most every :data:`REDRAW_SECONDS`. It draws nothing until the first download has
run for :data:`QUIET_SECONDS`, so a fast fetch leaves no trace, and nothing at all when the
stream is not a terminal (a pipe, a log file, ``ger serve``).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import TextIO


REDRAW_SECONDS = 0.1
QUIET_SECONDS = 0.5


def format_bytes(count: int) -> str:
    """``812 kB``, ``12.3 MB``: what a person reads at a glance."""
    if count < 1_000_000:
        return f"{count / 1000:.0f} kB"
    return f"{count / 1_000_000:.1f} MB"


class DownloadMeter:
    """``(url, received)`` callback that shows the bytes all downloads received so far."""

    def __init__(self, stream: TextIO, *, label: str, clock: Callable[[], float] = time.monotonic) -> None:
        self._stream = stream
        self._label = label
        self._clock = clock
        self._enabled = _is_terminal(stream)
        self._lock = threading.Lock()
        self._received: dict[str, int] = {}
        self._started: float | None = None
        self._drawn_at: float | None = None
        self._width = 0

    def __call__(self, url: str, received: int) -> None:
        if not self._enabled:
            return
        with self._lock:
            now = self._clock()
            self._received[url] = received
            if self._started is None:
                self._started = now
            if now - self._started < QUIET_SECONDS:
                return
            if self._drawn_at is not None and now - self._drawn_at < REDRAW_SECONDS:
                return
            self._drawn_at = now
            self._draw(self.line())

    def line(self) -> str:
        """What the meter shows now: the label, the bytes, and how many downloads they come from."""
        total = sum(self._received.values())
        count = len(self._received)
        logs = f" from {count} logs" if count > 1 else ""
        return f"{self._label}: {format_bytes(total)}{logs}"

    def close(self) -> None:
        """Erase the line, if one was drawn: the command's own output follows."""
        with self._lock:
            if self._width:
                self._stream.write("\r" + " " * self._width + "\r")
                self._stream.flush()
                self._width = 0

    def _draw(self, text: str) -> None:
        self._stream.write("\r" + text.ljust(self._width))
        self._stream.flush()
        self._width = max(self._width, len(text))


def _is_terminal(stream: TextIO) -> bool:
    try:
        return stream.isatty()
    except (AttributeError, ValueError):
        return False
