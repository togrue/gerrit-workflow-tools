"""Opening TCP connections to a host that has several addresses: ``connect`` and ``urlopen``.

A host often resolves to IPv6 and IPv4 addresses of which only some answer: a Gerrit in a
container that publishes its port on IPv4 only, say. Trying them one after the other, as
:func:`socket.create_connection` does, waits out every dead one first, and on Windows a
refused connection takes over two seconds to report, so each request paid seconds before it
was sent. :func:`connect` races the addresses instead (RFC 8305, "Happy Eyeballs"): when one
has not connected within :data:`ATTEMPT_DELAY` the next starts, the families alternating, and
the first to connect is kept.

The address that answered is remembered per host and port, and tried alone first the next
time. When it fails, or has not connected within :data:`PREFERRED_ROUTE_TIMEOUT`, it is
forgotten and the race starts over. This memo is a hint, not an answer (ADR-0014): it is
used only while the host still resolves to it, and each use checks it by connecting, so
there is nothing to invalidate.

:func:`urlopen` follows redirects, but a credential goes only to the origin it was sent to: a
redirect to another scheme, host or port is followed without it (``Authorization``, ``Cookie``).
"""

from __future__ import annotations

import errno
import http.client
import os
import selectors
import socket
import ssl
import sys
import threading
import time
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any, cast
from urllib.error import URLError
from urllib.parse import urlsplit


ATTEMPT_DELAY = 0.25
"""Seconds an attempt has before the next address is tried beside it (RFC 8305's default)."""

PREFERRED_ROUTE_TIMEOUT = 1.0
"""Seconds the remembered address has to connect before the race starts over."""

AddrInfo = tuple[int, int, int, str, tuple[Any, ...]]
Resolver = Callable[[str, int, int, int], Sequence[AddrInfo]]

_IN_PROGRESS = {errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EAGAIN, getattr(errno, "WSAEWOULDBLOCK", errno.EWOULDBLOCK)}

_routes: dict[tuple[str, int], tuple[Any, ...]] = {}
_routes_lock = threading.Lock()


def connect(
    address: tuple[str, int],
    timeout: object = None,
    source_address: tuple[str, int] | None = None,
    *,
    resolve: Resolver | None = None,
) -> socket.socket:
    """A socket connected to *address*, for :mod:`http.client` in place of :func:`socket.create_connection`.

    *timeout* bounds the connecting and is then the socket's timeout; anything but a number
    (the default, or :mod:`socket`'s sentinel) means :func:`socket.getdefaulttimeout`.
    """
    host, port = address[0], int(address[1])
    seconds = float(timeout) if isinstance(timeout, (int, float)) else socket.getdefaulttimeout()
    infos = list((resolve or socket.getaddrinfo)(host, port, 0, socket.SOCK_STREAM))
    if not infos:
        raise OSError(f"no address for {host}")
    key = (host, port)
    with _routes_lock:
        preferred = _routes.get(key)
    known = [info for info in infos if info[4] == preferred]
    if known:
        limit = PREFERRED_ROUTE_TIMEOUT if seconds is None else min(seconds, PREFERRED_ROUTE_TIMEOUT)
        try:
            sock, _ = _race(known, limit, source_address)
        except OSError:
            with _routes_lock:
                if _routes.get(key) == preferred:
                    del _routes[key]
        else:
            sock.settimeout(seconds)
            return sock
    sock, winner = _race(_interleaved(infos), seconds, source_address)
    with _routes_lock:
        _routes[key] = winner
    sock.settimeout(seconds)
    return sock


def _interleaved(infos: list[AddrInfo]) -> list[AddrInfo]:
    """The addresses in resolver order, but the families taking turns, the first family first."""
    families: dict[int, list[AddrInfo]] = {}
    for info in infos:
        families.setdefault(info[0], []).append(info)
    queues = list(families.values())
    order: list[AddrInfo] = []
    while queues:
        order.extend(queue.pop(0) for queue in queues)
        queues = [queue for queue in queues if queue]
    return order


def _race(
    infos: list[AddrInfo], limit: float | None, source_address: tuple[str, int] | None
) -> tuple[socket.socket, tuple[Any, ...]]:
    """Connect to the first of *infos* that answers, starting one every :data:`ATTEMPT_DELAY`."""
    deadline = None if limit is None else time.monotonic() + limit
    waiting = list(infos)
    attempts: dict[socket.socket, tuple[Any, ...]] = {}
    error: OSError | None = None
    next_start = time.monotonic()
    selector = selectors.DefaultSelector()
    try:
        while waiting or attempts:
            now = time.monotonic()
            if waiting and (now >= next_start or not attempts):
                family, kind, proto, _name, sockaddr = waiting.pop(0)
                try:
                    sock = _start(family, kind, proto, sockaddr, source_address)
                except OSError as failed:
                    error = failed
                    continue
                attempts[sock] = sockaddr
                selector.register(sock, selectors.EVENT_WRITE)
                next_start = now + ATTEMPT_DELAY
                continue
            if deadline is not None and now >= deadline:
                raise TimeoutError("timed out")
            waits = [moment - now for moment in (next_start if waiting else None, deadline) if moment is not None]
            for selected, _events in selector.select(max(0.0, min(waits)) if waits else None):
                sock = cast(socket.socket, selected.fileobj)
                selector.unregister(sock)
                sockaddr = attempts.pop(sock)
                code = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if code == 0:
                    sock.setblocking(True)
                    return sock, sockaddr
                sock.close()
                error = _failure(code)
        raise error or OSError("no address to connect to")
    finally:
        for sock in attempts:
            sock.close()
        selector.close()


def _start(
    family: int, kind: int, proto: int, sockaddr: tuple[Any, ...], source_address: tuple[str, int] | None
) -> socket.socket:
    """A socket whose connecting to *sockaddr* has begun."""
    sock = socket.socket(family, kind, proto)
    try:
        sock.setblocking(False)
        if source_address:
            sock.bind(source_address)
        code = sock.connect_ex(sockaddr)
        if code not in (0, *_IN_PROGRESS):
            raise _failure(code)
    except BaseException:
        sock.close()
        raise
    return sock


def _failure(code: int) -> OSError:
    """The error a failed connect reports by number, as the subclass ``connect`` would raise."""
    if sys.platform == "win32":
        return OSError(None, f"connect failed (WinError {code})", None, code)
    return OSError(code, os.strerror(code))


# http.client sets ``_create_connection`` on each connection in ``__init__``: replaced after it.
class _HTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._create_connection = connect


class _HTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._create_connection = connect


class _HTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        return self.do_open(_HTTPConnection, req)


class _HTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        return self.do_open(_HTTPSConnection, req)


CREDENTIAL_HEADERS = frozenset({"authorization", "proxy-authorization", "cookie"})
"""Request headers that are for the origin they were sent to, and nobody else."""

_DEFAULT_PORTS = {"http": 80, "https": 443}


def origin(url: str) -> tuple[str, str, int | None]:
    """``(scheme, host, port)`` of *url*, the default port spelled out: what a credential is scoped to."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    return scheme, (parts.hostname or "").lower(), parts.port or _DEFAULT_PORTS.get(scheme)


class _RedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follows redirects as urllib does, minus the credentials when the redirect leaves the origin.

    urllib copies every request header onto the redirected request, so a server that redirects
    elsewhere (an SSO front end, a log on other storage) would be handed the token for this one.
    """

    def redirect_request(
        self, req: urllib.request.Request, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> urllib.request.Request | None:
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and origin(new.full_url) != origin(req.full_url):
            for name in [name for name in new.headers if name.lower() in CREDENTIAL_HEADERS]:
                del new.headers[name]
        return new


_opener = urllib.request.build_opener(_HTTPHandler, _HTTPSHandler, _RedirectHandler)


def urlopen(request: str | urllib.request.Request, *, timeout: float) -> Any:
    """:func:`urllib.request.urlopen`, its connections made by :func:`connect`."""
    return _opener.open(request, timeout=timeout)


def why_unreachable(error: OSError) -> str:
    """A few words on why a connection failed, without the platform's localized prose."""
    reason = error.reason if isinstance(error, URLError) else error
    if isinstance(reason, ConnectionRefusedError):
        return "connection refused"
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return "timed out"
    if isinstance(reason, socket.gaierror):
        return "unknown host"
    if isinstance(reason, ssl.SSLError) and getattr(reason, "reason", None) == "WRONG_VERSION_NUMBER":
        return "it does not speak https on that port: an http:// server?"
    if isinstance(reason, OSError) and reason.strerror:
        return reason.strerror
    return str(reason)
