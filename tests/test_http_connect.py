"""Connections race a host's addresses and remember the one that answered (``core/http_connect.py``)."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itertools import count
from typing import Any

import pytest

from gerrit_workflow_tools.core import http_connect


_hosts = count()

Address = tuple[str, int]


def _host() -> str:
    """A host name of its own per test: what a host was reached on is remembered per name."""
    return f"gerrit-{next(_hosts)}.test"


def _resolving(*addresses: Address) -> Any:
    """A resolver that answers every name with *addresses*, in this order."""

    def resolve(_host: str, _port: int, *_args: object) -> list[tuple[Any, ...]]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", address) for address in addresses]

    return resolve


@pytest.fixture
def listeners() -> Iterator[list[socket.socket]]:
    opened: list[socket.socket] = []
    yield opened
    for sock in opened:
        sock.close()


def _listener(opened: list[socket.socket]) -> Address:
    """An address that accepts connections."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(16)
    opened.append(sock)
    return sock.getsockname()


def _refusing() -> Address:
    """An address nothing listens on: connecting is refused at once."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    address = sock.getsockname()
    sock.close()
    return address


def _hang(listener: Address, opened: list[socket.socket]) -> None:
    """Fill *listener*'s accept queue so that a new connection to it neither succeeds nor fails."""
    server = next(s for s in opened if s.getsockname() == listener)
    server.listen(0)
    for _ in range(8):
        probe = socket.socket()
        probe.settimeout(0.3)
        try:
            probe.connect(listener)
        except TimeoutError:
            probe.close()
            return
        opened.append(probe)  # holds its place in the queue
    pytest.skip("this platform does not leave a connection to a full accept queue hanging")


def _hanging(opened: list[socket.socket]) -> Address:
    address = _listener(opened)
    _hang(address, opened)
    return address


def _peer(sock: socket.socket) -> Address:
    with sock:
        return sock.getpeername()


def test_an_address_that_does_not_answer_does_not_hold_up_the_next(listeners: list[socket.socket]) -> None:
    silent, good = _hanging(listeners), _listener(listeners)
    started = time.monotonic()
    peer = _peer(http_connect.connect((_host(), 80), 10, resolve=_resolving(silent, good)))
    assert peer == good
    assert time.monotonic() - started < 1.0


def test_refused_addresses_are_passed_over_and_all_refused_is_refused(listeners: list[socket.socket]) -> None:
    good = _listener(listeners)
    assert _peer(http_connect.connect((_host(), 80), 10, resolve=_resolving(_refusing(), good))) == good
    with pytest.raises(ConnectionRefusedError):
        http_connect.connect((_host(), 80), 10, resolve=_resolving(_refusing(), _refusing()))


def test_the_address_that_answered_is_tried_first_next_time(listeners: list[socket.socket]) -> None:
    host, first, second = _host(), _listener(listeners), _listener(listeners)
    assert _peer(http_connect.connect((host, 80), 10, resolve=_resolving(_refusing(), second))) == second
    # Both answer now; without the memory the race would take the first.
    assert _peer(http_connect.connect((host, 80), 10, resolve=_resolving(first, second))) == second
    assert _peer(http_connect.connect((_host(), 80), 10, resolve=_resolving(first, second))) == first


def test_a_remembered_address_the_host_no_longer_resolves_to_is_not_tried(listeners: list[socket.socket]) -> None:
    host, first, second = _host(), _listener(listeners), _listener(listeners)
    assert _peer(http_connect.connect((host, 80), 10, resolve=_resolving(second))) == second
    assert _peer(http_connect.connect((host, 80), 10, resolve=_resolving(first))) == first


def test_a_remembered_address_that_now_refuses_starts_the_race_over(listeners: list[socket.socket]) -> None:
    host, first, second = _host(), _listener(listeners), _refusing()
    remembered = _listener(listeners)
    assert _peer(http_connect.connect((host, 80), 10, resolve=_resolving(remembered))) == remembered
    next(s for s in listeners if s.getsockname() == remembered).close()
    assert _peer(http_connect.connect((host, 80), 10, resolve=_resolving(second, first, remembered))) == first
    # The race's winner is what is remembered now.
    assert _peer(http_connect.connect((host, 80), 10, resolve=_resolving(_listener(listeners), first))) == first


def test_a_remembered_address_that_times_out_starts_the_race_over(
    listeners: list[socket.socket], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(http_connect, "PREFERRED_ROUTE_TIMEOUT", 0.3)
    host, remembered, other = _host(), _listener(listeners), _listener(listeners)
    assert _peer(http_connect.connect((host, 80), 10, resolve=_resolving(remembered))) == remembered
    _hang(remembered, listeners)
    started = time.monotonic()
    assert _peer(http_connect.connect((host, 80), 10, resolve=_resolving(remembered, other))) == other
    assert time.monotonic() - started < 1.5


def test_no_address_answering_in_time_is_a_timeout(listeners: list[socket.socket]) -> None:
    silent = _hanging(listeners)
    with pytest.raises(TimeoutError):
        http_connect.connect((_host(), 80), 0.3, resolve=_resolving(silent))


class _Hello(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # pylint: disable=invalid-name
        body = b"hello"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # pylint: disable=redefined-builtin
        pass


def test_urlopen_connects_through_the_race(listeners: list[socket.socket], monkeypatch: pytest.MonkeyPatch) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Hello)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        # One address after the other, the silent one would take the whole timeout.
        monkeypatch.setattr(socket, "getaddrinfo", _resolving(_hanging(listeners), ("127.0.0.1", port)))
        started = time.monotonic()
        with http_connect.urlopen(f"http://{_host()}:{port}/", timeout=5) as response:
            assert response.read() == b"hello"
        assert time.monotonic() - started < 1.0
    finally:
        server.shutdown()
        server.server_close()
