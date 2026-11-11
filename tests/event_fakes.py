"""A scripted Gerrit event stream: the stand-in for ``ssh … gerrit stream-events`` in tests.

Each connection plays a script: event lines, then how it ends — ``End(reason)`` (the stream
drops), ``HOLD`` (it stays open until closed, as a quiet live stream does) or, as the whole
script, an exception ``connect`` raises. ``Pushed`` lets a test feed lines while it runs.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Iterator
from dataclasses import dataclass


@dataclass(frozen=True)
class End:
    """The stream ends here, saying *reason* (what ssh would print on stderr)."""

    reason: str = "the stream ended"


HOLD = object()

Item = str | End | object


class ScriptedConnection:
    """One connection: plays its script, then holds or ends."""

    def __init__(self, script: list[Item]) -> None:
        self._script = script
        self._closed = threading.Event()
        self._failure = "the stream ended"
        self.closed_by_reader = False

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def lines(self) -> Iterator[str]:
        for item in self._script:
            if self._closed.is_set():
                return
            if isinstance(item, End):
                self._failure = item.reason
                return
            if item is HOLD:
                self._closed.wait()
                self._failure = "closed"
                return
            assert isinstance(item, str)
            yield item

    def failure(self) -> str:
        return self._failure

    def close(self) -> None:
        self._closed.set()


class Pushed(ScriptedConnection):
    """A connection whose lines the test pushes while it runs; ``drop`` ends it."""

    def __init__(self) -> None:
        super().__init__([])
        self._queue: queue.Queue[Item] = queue.Queue()

    def push(self, line: str) -> None:
        self._queue.put(line)

    def drop(self, reason: str) -> None:
        self._queue.put(End(reason))

    def lines(self) -> Iterator[str]:
        while not self._closed.is_set():
            try:
                item = self._queue.get(timeout=0.02)
            except queue.Empty:
                continue
            if isinstance(item, End):
                self._failure = item.reason
                return
            assert isinstance(item, str)
            yield item

    def close(self) -> None:
        self._closed.set()


class ScriptedEventSource:
    """Hands out scripted connections in order; past the last one, connections that hold."""

    description = "ssh://events.example:29418"

    def __init__(self, *scripts: list[Item] | ScriptedConnection | BaseException) -> None:
        self._scripts = list(scripts)
        self.connections: list[ScriptedConnection] = []
        self.attempts = 0
        self._connected = threading.Condition()

    def connect(self) -> ScriptedConnection:
        with self._connected:
            self.attempts += 1
            self._connected.notify_all()
            script = self._scripts.pop(0) if self._scripts else [HOLD]
            if isinstance(script, BaseException):
                raise script
            connection = script if isinstance(script, ScriptedConnection) else ScriptedConnection(script)
            self.connections.append(connection)
            return connection

    def wait_attempts(self, n: int, timeout: float = 5.0) -> bool:
        """Wait until *n* connections were attempted."""
        with self._connected:
            return self._connected.wait_for(lambda: self.attempts >= n, timeout)
