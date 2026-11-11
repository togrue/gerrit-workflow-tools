"""Gerrit's event stream: parsing, what an event does to the cache, the reader's states, and ssh."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.cache import GerritCache
from gerrit_workflow_tools.core.gerrit.event_source import (
    DEFAULT_SSH_PORT,
    SUBSCRIBED_EVENTS,
    EventSourceUnavailable,
    SshEventSource,
    SshTarget,
    discover_ssh_target,
)
from gerrit_workflow_tools.core.gerrit.event_stream import (
    CHANGE_EVENTS,
    EventScope,
    EventStream,
    GerritEvent,
    StreamMode,
    StreamState,
    StreamStatus,
    apply_event,
    parse_event,
)
from tests.event_fakes import HOLD, End, Pushed, ScriptedEventSource


CID_A = "I" + "a" * 40
CID_B = "I" + "b" * 40


def event_line(kind: str, number: int = 101, *, project: str = "proj", change_id: str = CID_B, **extra: Any) -> str:
    """One stream line in the shape cmd-stream-events documents."""
    change = {"project": project, "branch": "main", "id": change_id, "number": number, "owner": {"username": "alice"}}
    return json.dumps({"type": kind, "change": change, "eventCreatedOn": 1, **extra})


# --- Parsing -------------------------------------------------------------------------


def test_an_event_line_is_reduced_to_what_decides_whether_it_matters() -> None:
    event = parse_event(event_line("comment-added", author={"username": "Bob", "email": "bob@example.com"}))
    assert event == GerritEvent(
        type="comment-added",
        project="proj",
        branch="main",
        change_id=CID_B,
        number=101,
        accounts=frozenset({"alice", "bob", "bob@example.com"}),
    )
    ref = parse_event(
        json.dumps({"type": "ref-updated", "refUpdate": {"project": "proj", "refName": "refs/heads/main"}})
    )
    assert ref == GerritEvent(type="ref-updated", project="proj", ref_name="refs/heads/main")


@pytest.mark.parametrize(
    "line",
    ["", "not json", '{"type": "comment-added", "change": {"num', "[1, 2]", '{"change": {}}', '{"type": ""}', "null"],
)
def test_garbage_and_partial_lines_are_not_events(line: str) -> None:
    assert parse_event(line) is None


def test_missing_and_ill_typed_fields_read_as_unknown() -> None:
    event = parse_event('{"type": "patchset-created", "change": {"number": "12", "project": 7}, "uploader": "x"}')
    assert event == GerritEvent(type="patchset-created")


def test_every_change_event_is_subscribed_to() -> None:
    assert set(SUBSCRIBED_EVENTS) >= CHANGE_EVENTS
    assert {"ref-updated", "dropped-output"} <= set(SUBSCRIBED_EVENTS)


# --- What an event does to the cache --------------------------------------------------


def _payload(number: int, change_id: str) -> dict[str, Any]:
    return {"id": f"proj~{number}", "change_id": change_id, "branch": "main", "_number": number, "updated": "u1"}


@pytest.fixture
def cache(tmp_path: Path) -> GerritCache:
    cache = GerritCache(tmp_path / "cache.db", web_base="https://g.example")
    for number, change_id in ((100, CID_A), (101, CID_B)):
        payload = _payload(number, change_id)
        # Cached under the lookup triplet, Gerrit's own id and the bare number, as callers do.
        cache.upsert_changes({f"proj~main~{change_id}": payload, str(number): payload})
        cache.upsert_comments(payload["id"], {"a.txt": [{"id": f"c{number}"}]}, change_updated="u1")
    return cache


def _load(cache: GerritCache, triplets: list[str]) -> tuple[dict[str, Any], list[str]]:
    fetched: list[str] = []

    def fetch(ids: list[str]) -> dict[str, dict[str, Any]]:
        fetched.extend(ids)
        return {ref: _payload(int(ref.split("~")[1]) if ref.count("~") == 1 else 101, CID_B) for ref in ids}

    def probe(_ids: list[str]) -> dict[str, str]:
        raise AssertionError("inside the trust window nothing is probed")

    return cache.load_changes(triplets, probe_updated=probe, fetch_changes=fetch, trust_window_seconds=3600), fetched


def test_an_event_drops_exactly_the_change_it_is_about(cache: GerritCache) -> None:
    scope = EventScope.of(["other"])
    event = parse_event(event_line("comment-added", 101))
    assert event is not None
    assert apply_event(event, cache=cache, scope=scope) is True  # the cache had it: look again

    _rows, fetched = _load(cache, [f"proj~main~{CID_A}", f"proj~main~{CID_B}"])
    assert fetched == [f"proj~main~{CID_B}"]  # 100 still served from the trust window, 101 asked again
    assert cache.load_comments("proj~100", fetch_comments=lambda _t: {}, change_updated="u1") != {}
    assert cache.load_comments("proj~101", fetch_comments=lambda _t: {}, change_updated="u1") == {}
    assert cache.info().changes == 3 + 2  # 100's three keys; 101 again, under the key asked for and its own id


def test_an_event_only_narrows_trust_it_never_populates(cache: GerritCache) -> None:
    before = cache.info()
    event = parse_event(event_line("patchset-created", 555, project="proj", change_id=CID_A))
    assert event is not None
    apply_event(event, cache=cache, scope=EventScope.of(["proj"]))
    assert cache.info() == before


def test_an_event_clears_a_no_such_change_verdict(tmp_path: Path) -> None:
    cache = GerritCache(tmp_path / "cache.db", web_base="https://g.example")
    triplet = f"proj~main~{CID_A}"
    asked: list[list[str]] = []

    def fetch(ids: list[str]) -> dict[str, dict[str, Any]]:
        asked.append(ids)
        return {}

    load = lambda: cache.load_changes([triplet], probe_updated=lambda _i: {}, fetch_changes=fetch)  # noqa: E731
    load()
    load()
    assert asked == [[triplet]]  # "no such change" is remembered for a while
    event = parse_event(event_line("patchset-created", 7, change_id=CID_A))
    assert event is not None
    assert apply_event(event, cache=cache, scope=EventScope.of([])) is True
    load()
    assert asked == [[triplet], [triplet]]  # someone pushed it: asked again at once


@pytest.mark.parametrize(
    ("line", "wanted"),
    [
        (event_line("comment-added", 900, project="elsewhere"), False),  # not cached, not ours, not about you
        (event_line("comment-added", 900, project="proj"), True),  # a change of this repository's project
        (event_line("reviewer-added", 900, project="elsewhere", reviewer={"username": "Me"}), True),  # asks you
        (json.dumps({"type": "ref-updated", "refUpdate": {"project": "proj", "refName": "refs/heads/main"}}), True),
        (
            json.dumps({"type": "ref-updated", "refUpdate": {"project": "proj", "refName": "refs/changes/01/1/1"}}),
            False,
        ),
        (
            json.dumps({"type": "ref-updated", "refUpdate": {"project": "elsewhere", "refName": "refs/heads/main"}}),
            False,
        ),
        (json.dumps({"type": "dropped-output"}), True),  # events were lost: anything may have moved
        (json.dumps({"type": "project-created", "projectName": "proj"}), False),  # not a type ger follows
        (json.dumps({"type": "comment-added"}), False),  # no change attribute at all
    ],
)
def test_which_events_make_a_client_look_again(tmp_path: Path, line: str, wanted: bool) -> None:
    cache = GerritCache(tmp_path / "cache.db", web_base="https://g.example")
    event = parse_event(line)
    assert event is not None
    assert apply_event(event, cache=cache, scope=EventScope.of(["proj"], ["me", None])) is wanted


# --- The reader ------------------------------------------------------------------------


class Recorder:
    def __init__(self) -> None:
        self.events: list[GerritEvent] = []
        self.statuses: list[StreamStatus] = []
        self._changed = threading.Condition()

    def on_event(self, event: GerritEvent) -> None:
        with self._changed:
            self.events.append(event)
            self._changed.notify_all()

    def on_status(self, status: StreamStatus) -> None:
        with self._changed:
            self.statuses.append(status)
            self._changed.notify_all()

    def states(self) -> list[str]:
        return [status.state.value for status in self.statuses]

    def wait(self, predicate: Any, timeout: float = 5.0) -> bool:
        with self._changed:
            return self._changed.wait_for(predicate, timeout)


def _stream(source: ScriptedEventSource, mode: StreamMode, recorder: Recorder, **kwargs: Any) -> EventStream:
    kwargs.setdefault("grace", 30.0)
    kwargs.setdefault("backoff", (0.01, 0.05))
    return EventStream(source, mode=mode, on_event=recorder.on_event, on_status=recorder.on_status, **kwargs)


def test_lines_arrive_in_order_garbage_and_unknown_types_skipped_or_passed_through() -> None:
    recorder = Recorder()
    lines = [event_line("comment-added", 1), "{garbage", event_line("patchset-created", 2), '{"type": "brand-new"}']
    stream = _stream(ScriptedEventSource([*lines, HOLD]), StreamMode.AUTO, recorder)
    stream.start()
    assert recorder.wait(lambda: len(recorder.events) == 3)
    stream.stop()
    assert [(e.type, e.number) for e in recorder.events] == [
        ("comment-added", 1),
        ("patchset-created", 2),
        ("brand-new", None),
    ]
    assert recorder.states() == ["connecting", "live"]


def test_a_quiet_stream_is_live_once_it_stays_open_for_the_grace_period() -> None:
    recorder = Recorder()
    stream = _stream(ScriptedEventSource([HOLD]), StreamMode.AUTO, recorder, grace=0.05)
    stream.start()
    assert recorder.wait(lambda: recorder.states() == ["connecting", "live"])
    assert stream.status.state is StreamState.LIVE
    stream.stop()


def test_auto_tries_once_and_gives_up_quietly_when_it_never_gets_live() -> None:
    recorder = Recorder()
    source = ScriptedEventSource([End("Permission denied (publickey).")])
    stream = _stream(source, StreamMode.AUTO, recorder)
    stream.start()
    assert recorder.wait(lambda: "unavailable" in recorder.states())
    stream.stop()
    assert stream.status == StreamStatus(StreamState.UNAVAILABLE, "Permission denied (publickey).", source.description)
    assert source.attempts == 1
    assert "Permission denied" in stream.status.sentence() and "polling" in stream.status.sentence()


def test_auto_without_ssh_is_unavailable_and_says_why() -> None:
    recorder = Recorder()
    stream = _stream(ScriptedEventSource(EventSourceUnavailable("no ssh program on PATH")), StreamMode.AUTO, recorder)
    stream.start()
    assert recorder.wait(lambda: "unavailable" in recorder.states())
    stream.stop()
    assert stream.status.reason == "no ssh program on PATH"


def test_on_keeps_trying_and_says_so_until_it_connects() -> None:
    recorder = Recorder()
    denied = End('fatal: alice does not have "Stream Events" capability.')
    source = ScriptedEventSource([denied], [denied], [event_line("comment-added", 5), HOLD])
    stream = _stream(source, StreamMode.ON, recorder)
    stream.start()
    assert recorder.wait(lambda: len(recorder.events) == 1)
    stream.stop()
    assert recorder.states() == ["connecting", "retrying", "connecting", "retrying", "connecting", "live"]
    assert recorder.statuses[1].reason == denied.reason


def test_a_dropped_stream_reconnects_with_backoff_even_in_auto() -> None:
    recorder = Recorder()
    first = Pushed()
    source = ScriptedEventSource(first, [event_line("change-merged", 9), HOLD])
    stream = _stream(source, StreamMode.AUTO, recorder)
    stream.start()
    first.push(event_line("comment-added", 8))
    assert recorder.wait(lambda: len(recorder.events) == 1)
    first.drop("Connection to events.example closed by remote host.")
    assert recorder.wait(lambda: len(recorder.events) == 2)
    stream.stop()
    assert recorder.states() == ["connecting", "live", "retrying", "connecting", "live"]
    assert recorder.statuses[2].reason == "Connection to events.example closed by remote host."


def test_a_handler_that_fails_does_not_stop_the_stream() -> None:
    seen: list[int | None] = []

    def handler(event: GerritEvent) -> None:
        seen.append(event.number)
        if event.number == 1:
            raise RuntimeError("bug")

    stream = EventStream(
        ScriptedEventSource([event_line("comment-added", 1), event_line("comment-added", 2), HOLD]),
        mode=StreamMode.AUTO,
        on_event=handler,
    )
    stream.start()
    deadline = time.monotonic() + 5
    while len(seen) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    stream.stop()
    assert seen == [1, 2]


def test_stop_ends_the_connection_and_returns_promptly() -> None:
    recorder = Recorder()
    source = ScriptedEventSource([event_line("comment-added", 1), HOLD])
    stream = _stream(source, StreamMode.ON, recorder)
    stream.start()
    assert recorder.wait(lambda: recorder.events)
    started = time.monotonic()
    stream.stop()
    assert time.monotonic() - started < 1.0
    assert source.connections[0].closed
    assert source.attempts == 1  # no reconnect after a stop


def test_stop_during_backoff_returns_promptly() -> None:
    recorder = Recorder()
    source = ScriptedEventSource([End("refused")])
    stream = _stream(source, StreamMode.ON, recorder, backoff=(30.0, 60.0))
    stream.start()
    assert recorder.wait(lambda: "retrying" in recorder.states())
    started = time.monotonic()
    stream.stop()
    assert time.monotonic() - started < 1.0


def test_off_never_connects() -> None:
    source = ScriptedEventSource()
    stream = EventStream(source, mode=StreamMode.OFF, on_event=lambda _e: None)
    stream.start()
    stream.stop()
    assert source.attempts == 0
    assert stream.status.state is StreamState.OFF and stream.status.reason == "ger.eventStream is off"
    assert EventStream(None, mode=StreamMode.AUTO, on_event=lambda _e: None, off_reason="why").status.reason == "why"


# --- Where the stream is, from what ger already knows -----------------------------------


@pytest.mark.parametrize(
    ("config", "target"),
    [
        (
            {
                "gerrit.webUrl": "https://review.example.com",
                "remote.origin.url": "https://review.example.com/a/proj",
                "gerrit.user": "me",
            },
            SshTarget("review.example.com", DEFAULT_SSH_PORT, "me"),
        ),
        (
            {"remote.origin.url": "ssh://bob@review.example.com:29418/proj"},
            SshTarget("review.example.com", 29418, "bob"),
        ),
        (
            {"remote.origin.url": "ssh://review.example.com/proj", "gerrit.user": "me"},
            SshTarget("review.example.com", None, "me"),
        ),
        ({"remote.origin.url": "gerrit:proj.git"}, SshTarget("gerrit", None, None)),  # an ssh config alias
        ({"remote.origin.url": "git@host.example:proj"}, SshTarget("host.example", None, "git")),
        (
            {
                "gerrit.webUrl": "https://review.example.com",
                "ger.eventStreamHost": "ssh.example.com",
                "ger.eventStreamPort": "2222",
                "ger.eventStreamUser": "events",
            },
            SshTarget("ssh.example.com", 2222, "events"),
        ),
        (
            {"remote.origin.url": "C:\\repos\\proj", "gerrit.webUrl": "http://g.example:8080"},
            SshTarget("g.example", 29418),
        ),
        ({"gerrit.remote": "gerrit", "remote.gerrit.url": "ssh://g.example:29419/p"}, SshTarget("g.example", 29419)),
    ],
)
def test_the_ssh_target_comes_from_overrides_the_remote_or_the_web_url(
    config: dict[str, str], target: SshTarget
) -> None:
    assert discover_ssh_target(Settings.from_map(config)) == target


@pytest.mark.parametrize(
    ("config", "reason"),
    [
        ({}, "no Gerrit host"),
        ({"gerrit.webUrl": "https://g.example", "ger.eventStreamPort": "ssh"}, "not a port"),
    ],
)
def test_no_ssh_target_says_why(config: dict[str, str], reason: str) -> None:
    with pytest.raises(EventSourceUnavailable, match=reason):
        discover_ssh_target(Settings.from_map(config))


def test_ssh_runs_without_prompts_or_a_tty_and_subscribes_only_to_what_matters() -> None:
    argv = SshTarget("g.example", 29418, "me").argv()
    assert argv[:2] == ["ssh", "-T"]
    assert {"BatchMode=yes", "ConnectTimeout=10"} <= {argv[i + 1] for i, a in enumerate(argv) if a == "-o"}
    assert argv[argv.index("-p") + 1] == "29418" and argv[argv.index("-l") + 1] == "me"
    tail = argv[argv.index("g.example") :]
    assert tail[:3] == ["g.example", "gerrit", "stream-events"]
    assert [tail[i + 1] for i, a in enumerate(tail) if a == "-s"] == list(SUBSCRIBED_EVENTS)
    assert SshTarget("alias").argv("ssh")[-len(SUBSCRIBED_EVENTS) * 2 - 3 :][:3] == ["alias", "gerrit", "stream-events"]


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("auto", "auto"), ("", "auto"), ("maybe", "auto"), ("on", "on"), ("true", "on"), ("off", "off"), ("0", "off")],
)
def test_the_mode_is_a_tri_state_defaulting_to_auto(mode: str, expected: str) -> None:
    assert Settings.from_map({"ger.eventStream": mode}).event_stream == expected
    assert StreamMode(expected).value == expected


def test_no_ssh_program_is_unavailable() -> None:
    with pytest.raises(EventSourceUnavailable, match="no ssh program"):
        SshEventSource(SshTarget("g.example"), ssh="ssh-that-is-not-installed").connect()


# --- The ssh child --------------------------------------------------------------------------


def _fake_ssh(tmp_path: Path, body: str) -> str:
    """An executable that stands in for ssh: ignores its arguments, runs *body*."""
    path = tmp_path / "ssh"
    path.write_text(f"#!{sys.executable}\nimport sys, time\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


posix_only = pytest.mark.skipif(os.name != "posix", reason="the fake ssh is a script with a #! line")


@posix_only
def test_ssh_stdout_is_the_stream_and_stderr_says_why_it_ended(tmp_path: Path) -> None:
    body = "\n".join(
        [
            'print(\'{"type": "x"}\', flush=True)',
            "print('Permission denied (publickey).', file=sys.stderr)",
            "sys.exit(255)",
        ]
    )
    connection = SshEventSource(SshTarget("g.example"), ssh=_fake_ssh(tmp_path, body)).connect()
    assert list(connection.lines()) == ['{"type": "x"}']
    assert connection.failure() == "Permission denied (publickey)."
    connection.close()


@posix_only
def test_closing_stops_the_ssh_child_so_none_is_left_behind(tmp_path: Path) -> None:
    connection = SshEventSource(SshTarget("g.example"), ssh=_fake_ssh(tmp_path, "time.sleep(60)")).connect()
    reader = threading.Thread(target=lambda: list(connection.lines()))
    reader.start()
    started = time.monotonic()
    connection.close()
    reader.join(timeout=5)
    assert not reader.is_alive() and time.monotonic() - started < 5
    assert connection._process.poll() is not None  # type: ignore[attr-defined]  # pylint: disable=protected-access
