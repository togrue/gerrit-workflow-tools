"""Following Gerrit's **event stream**: one background reader, reconnects, and what an event means.

An event is news that a change moved. It may only narrow the **trust window**: the change's
cached rows are dropped, so the next read asks Gerrit; an event never puts data into the cache
and never makes a cached value count as fresh (ADR-0012). Whatever happens to the stream —
no ssh, no key, no capability, a dropped connection — ger works exactly as without it.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from gerrit_workflow_tools.core.gerrit.cache import GerritCache
from gerrit_workflow_tools.core.gerrit.event_source import EventConnection, EventSource, EventSourceUnavailable


logger = logging.getLogger(__name__)

# Events about one change; each carries a ``change`` attribute (cmd-stream-events).
CHANGE_EVENTS = frozenset(
    {
        "patchset-created",
        "comment-added",
        "change-merged",
        "change-abandoned",
        "change-restored",
        "change-deleted",
        "reviewer-added",
        "reviewer-deleted",
        "vote-deleted",
        "topic-changed",
        "hashtags-changed",
        "wip-state-changed",
        "private-state-changed",
    }
)
# The account attributes an event may carry: who did it, or whom it is about.
_ACCOUNT_FIELDS: tuple[str, ...] = ("uploader", "author", "submitter", "abandoner", "restorer", "deleter")
_ACCOUNT_FIELDS += ("editor", "changer", "reviewer", "adder", "remover")

GRACE_SECONDS = 5.0
BACKOFF_SECONDS = (1.0, 60.0)


class StreamMode(Enum):
    """``ger.eventStream``: try quietly once (``auto``), keep trying and say so (``on``), never (``off``)."""

    AUTO = "auto"
    ON = "on"
    OFF = "off"


class StreamState(Enum):
    """Where the event stream is. Only ``live`` changes what a client does."""

    OFF = "off"
    CONNECTING = "connecting"
    LIVE = "live"
    RETRYING = "retrying"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class StreamStatus:
    """The stream's state, why (``reason``, for anything but ``live``), and where it comes from."""

    state: StreamState
    reason: str | None = None
    source: str | None = None

    def sentence(self) -> str:
        """One line for a log: what it means for a client."""
        where = f" ({self.source})" if self.source else ""
        if self.state is StreamState.LIVE:
            return f"Gerrit events are live{where}: views refresh when something happens"
        if self.state is StreamState.CONNECTING:
            return f"connecting to Gerrit's event stream{where}"
        why = f": {self.reason}" if self.reason else ""
        if self.state is StreamState.RETRYING:
            return f"Gerrit's event stream is down{where}{why}; polling until it is back"
        return f"no Gerrit event stream{where}{why}; polling"


@dataclass(frozen=True)
class GerritEvent:
    """One event of the stream, reduced to what decides whether it matters here."""

    type: str
    project: str | None = None
    branch: str | None = None
    change_id: str | None = None
    number: int | None = None
    ref_name: str | None = None
    accounts: frozenset[str] = frozenset()


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _account_names(event: dict[str, Any], change: dict[str, Any]) -> frozenset[str]:
    rows = [event.get(field) for field in _ACCOUNT_FIELDS] + [change.get("owner")]
    names = {_text(row.get(key)) for row in rows if isinstance(row, dict) for key in ("username", "email")}
    return frozenset(name.lower() for name in names if name)


def parse_event(line: str) -> GerritEvent | None:
    """The event on one stream line, or ``None`` for anything that is not one (any field may be missing)."""
    try:
        raw = json.loads(line)
    except ValueError:
        return None
    if not isinstance(raw, dict) or not _text(raw.get("type")):
        return None
    change: dict[str, Any] = raw["change"] if isinstance(raw.get("change"), dict) else {}
    ref: dict[str, Any] = raw["refUpdate"] if isinstance(raw.get("refUpdate"), dict) else {}
    number = change.get("number")
    return GerritEvent(
        type=raw["type"],
        project=_text(change.get("project")) or _text(ref.get("project")),
        branch=_text(change.get("branch")),
        change_id=_text(change.get("id")),
        number=number if isinstance(number, int) and not isinstance(number, bool) else None,
        ref_name=_text(ref.get("refName")),
        accounts=_account_names(raw, change),
    )


@dataclass(frozen=True)
class EventScope:
    """What this repository cares about: its Gerrit projects, and who "you" are on Gerrit."""

    projects: frozenset[str]
    accounts: frozenset[str] = frozenset()

    @classmethod
    def of(cls, projects: Iterable[str | None], accounts: Iterable[str | None] = ()) -> EventScope:
        """A scope from whatever is known; unknown (``None``) entries are left out."""
        return cls(frozenset(p for p in projects if p), frozenset(a.lower() for a in accounts if a))


def apply_event(event: GerritEvent, *, cache: GerritCache, scope: EventScope) -> bool:
    """Drop what *cache* holds on the change *event* is about; say whether a client should look again.

    It should when the cache had the change, the change is in one of the scope's projects, or the
    event names you (a new review request); when a branch of those projects moved; and when Gerrit
    says it dropped events. Anything else is somebody else's business and is ignored.
    """
    if event.type == "dropped-output":
        return True
    if event.type == "ref-updated":
        return event.project in scope.projects and (event.ref_name or "").startswith("refs/heads/")
    if event.type not in CHANGE_EVENTS or event.number is None:
        return False
    cached = cache.forget_change(event.number, event.change_id)
    return cached or event.project in scope.projects or bool(event.accounts & scope.accounts)


class EventStream:
    """One background reader of an :class:`EventSource`, reconnecting with capped backoff.

    ``auto`` gives up for good when the first connection never becomes live; ``on`` keeps
    trying. Once a stream has been live, a drop is retried in either mode. *on_event* gets
    every parsed event, *on_status* every change of :class:`StreamStatus`; both run on the
    reader thread and must not block for long. A connection that stays open *grace* seconds,
    or delivers a line, is live: ``stream-events`` prints nothing until something happens.
    """

    def __init__(
        self,
        source: EventSource | None,
        *,
        mode: StreamMode,
        on_event: Callable[[GerritEvent], None],
        on_status: Callable[[StreamStatus], None] = lambda _status: None,
        off_reason: str | None = None,
        grace: float = GRACE_SECONDS,
        backoff: tuple[float, float] = BACKOFF_SECONDS,
    ) -> None:
        self._source = source if mode is not StreamMode.OFF else None
        self._mode = mode
        self._on_event = on_event
        self._on_status = on_status
        self._grace = grace
        self._backoff = backoff
        self._lock = threading.Lock()
        self._stopping = threading.Event()
        self._connection: EventConnection | None = None
        self._thread: threading.Thread | None = None
        where = source.description if source is not None else None
        reason = "ger.eventStream is off" if mode is StreamMode.OFF else off_reason
        state = StreamState.OFF if self._source is None else StreamState.CONNECTING
        self._status = StreamStatus(state, reason if state is StreamState.OFF else None, where)

    @property
    def status(self) -> StreamStatus:
        """Where the stream is now."""
        with self._lock:
            return self._status

    def start(self) -> None:
        """Start the reader thread; a stream that is off stays off."""
        if self._source is None or self._thread is not None:
            return
        self._on_status(self.status)
        self._thread = threading.Thread(target=self._run, name="ger-events", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Stop reading and end the connection (its ssh child too); returns within *timeout*."""
        self._stopping.set()
        with self._lock:
            connection = self._connection
        if connection is not None:
            connection.close()
        if self._thread is not None:
            self._thread.join(timeout)

    def _set(self, state: StreamState, reason: str | None = None) -> None:
        with self._lock:
            if self._stopping.is_set() or (self._status.state, self._status.reason) == (state, reason):
                return
            self._status = StreamStatus(state, reason, self._status.source)
            status = self._status
        self._on_status(status)

    def _run(self) -> None:
        assert self._source is not None
        attempt = 0
        ever_live = False
        while not self._stopping.is_set():
            self._set(StreamState.CONNECTING)
            live, failure = self._follow(self._source)
            ever_live = ever_live or live
            if self._stopping.is_set():
                return
            if not ever_live and self._mode is StreamMode.AUTO:
                self._set(StreamState.UNAVAILABLE, failure)
                return
            attempt = 0 if live else attempt + 1
            self._set(StreamState.RETRYING, failure)
            self._stopping.wait(min(self._backoff[1], self._backoff[0] * 2**attempt))

    def _follow(self, source: EventSource) -> tuple[bool, str]:
        """Read one connection to its end: whether it was live, and why it ended."""
        try:
            connection = source.connect()
        except EventSourceUnavailable as error:
            return False, str(error)
        with self._lock:
            self._connection = connection
        if self._stopping.is_set():
            connection.close()
        ended = threading.Event()
        live = threading.Event()

        def promote() -> None:
            if not ended.wait(self._grace) and not live.is_set():
                live.set()
                self._set(StreamState.LIVE)

        threading.Thread(target=promote, name="ger-events-grace", daemon=True).start()
        broken: str | None = None
        try:
            for line in connection.lines():
                if not live.is_set():
                    live.set()
                    self._set(StreamState.LIVE)
                self._deliver(line)
        except Exception as error:  # pylint: disable=broad-exception-caught  # never the end of ger serve
            logger.debug("event stream read failed: %s", error, exc_info=True)
            broken = f"reading the stream failed: {error}"
        finally:
            ended.set()
            with self._lock:
                self._connection = None
        failure = connection.failure()
        connection.close()
        return live.is_set(), broken or failure

    def _deliver(self, line: str) -> None:
        event = parse_event(line)
        if event is None:
            if line.strip():
                logger.debug("event stream: not an event: %.200s", line)
            return
        try:
            self._on_event(event)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("handling a Gerrit event failed: %s", event)
