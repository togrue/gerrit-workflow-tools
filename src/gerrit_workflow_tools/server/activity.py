"""The **activity sequence** of ``ger serve``, and the **event stream** that raises it.

A client that remembers the last sequence it saw asks ``GET /activity?since=N&wait=S`` and
learns, as soon as it happens, that it should look again. Without an event stream the number
never moves and the client polls as before. Spec: docu/spec/commands/serve.md.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.cache import GerritCache
from gerrit_workflow_tools.core.gerrit.event_source import (
    EventSource,
    EventSourceUnavailable,
    SshEventSource,
    discover_ssh_target,
)
from gerrit_workflow_tools.core.gerrit.event_stream import (
    EventScope,
    EventStream,
    GerritEvent,
    StreamMode,
    StreamState,
    StreamStatus,
    apply_event,
)
from gerrit_workflow_tools.core.gerrit.rest import GerritRest
from gerrit_workflow_tools.core.gerrit_project_id import resolve_gerrit_project_name


MAX_WAIT_SECONDS = 30
_NOT_FOLLOWED = StreamStatus(StreamState.OFF, "not followed")


class Activity:
    """A number that goes up whenever a client should look again; long-polled, never blocking a request."""

    def __init__(self) -> None:
        self._changed = threading.Condition()
        self._sequence = 0
        self._closed = False
        self.status: Callable[[], StreamStatus] = lambda: _NOT_FOLLOWED

    @property
    def sequence(self) -> int:
        """The current activity sequence."""
        with self._changed:
            return self._sequence

    def bump(self) -> None:
        """Something moved: wake every waiting client."""
        with self._changed:
            self._sequence += 1
            self._changed.notify_all()

    def wait(self, since: int | None, seconds: float) -> int:
        """The sequence once it differs from *since*, or after *seconds*, or when the service stops."""
        with self._changed:
            if since is not None:
                self._changed.wait_for(lambda: self._closed or self._sequence != since, min(seconds, MAX_WAIT_SECONDS))
            return self._sequence

    def close(self) -> None:
        """The service stops: let every waiting client go."""
        with self._changed:
            self._closed = True
            self._changed.notify_all()

    def json(self) -> dict[str, Any]:
        """``activity`` and ``events`` (the stream's ``state``, ``reason``, ``source``), as served."""
        status = self.status()
        events = {"state": status.state.value, "reason": status.reason, "source": status.source}
        return {"activity": self.sequence, "events": events}


def follow_events(
    cwd: Path,
    settings: Settings,
    *,
    activity: Activity,
    lock: threading.Lock,
    gerrit: GerritRest | None = None,
    source: EventSource | None = None,
    say: Callable[[StreamStatus], None] = lambda _status: None,
) -> EventStream:
    """The event stream for the repository at *cwd*, wired to *activity*; not started yet.

    An event is applied to the Gerrit cache under *lock*, the lock requests run under, so a
    request that read Gerrit before the event cannot store its answer after the event dropped
    it. With an injected *gerrit* (tests) there is no ssh to try: only an injected *source*.
    *say* gets each status worth a log line: not ``connecting``, and nothing twice in a row.
    """
    mode = StreamMode(settings.event_stream)
    web_base = gerrit.web_base if gerrit is not None else settings.gerrit_web_url
    you = [settings.gerrit_user]
    off_reason: str | None = None
    if not web_base:
        source, off_reason = None, "gerrit.webUrl is not set"
    elif source is None and gerrit is not None:
        off_reason = "no event source for an injected Gerrit"
    elif source is None and mode is not StreamMode.OFF:
        try:
            target = discover_ssh_target(settings)
        except EventSourceUnavailable as error:
            off_reason = str(error)
        else:
            source = SshEventSource(target)
            you.append(target.user)
    cache = GerritCache.for_web_base(web_base) if web_base and source is not None else None
    scope = EventScope.of([resolve_gerrit_project_name(cwd, settings=settings), *settings.inbox_projects], you)

    def on_event(event: GerritEvent) -> None:
        if cache is not None:
            with lock:
                wanted = apply_event(event, cache=cache, scope=scope)
            if wanted:
                activity.bump()

    last: dict[str, Any] = {"state": None, "said": None}

    def on_status(status: StreamStatus) -> None:
        # A client switches between following and polling: tell it whenever "live" flips or ends.
        if StreamState.LIVE in (last["state"], status.state) or status.state is StreamState.UNAVAILABLE:
            activity.bump()
        last["state"] = status.state
        key = (status.state, status.reason)
        if status.state is not StreamState.CONNECTING and last["said"] != key:
            last["said"] = key
            say(status)

    stream = EventStream(source, mode=mode, on_event=on_event, on_status=on_status, off_reason=off_reason)
    activity.status = lambda: stream.status
    return stream
