"""A credential goes only to the origin it was sent to: both HTTP clients follow a redirect elsewhere without it.

The Gerrit REST client and the CI client share one opener (``core/http_connect.py``); this pins
that they agree, against two real servers.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from gerrit_workflow_tools.core.ci_http import UrllibCiHttp, authorization_header
from gerrit_workflow_tools.core.gerrit.credentials import GerritAuth
from gerrit_workflow_tools.core.gerrit.http_rest import HttpGerritRest
from gerrit_workflow_tools.core.http_connect import origin


class _Server(ThreadingHTTPServer):
    """Answers ``{}``; redirects to :attr:`elsewhere` first, when set. Remembers the headers of what it answered."""

    elsewhere: str | None = None

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.answered: list[dict[str, str]] = []

    @property
    def url(self) -> str:
        """The server's origin."""
        return f"http://127.0.0.1:{self.server_address[1]}"


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # pylint: disable=invalid-name
        server = self.server
        assert isinstance(server, _Server)
        if server.elsewhere and not self.path.startswith("/landed"):
            self.send_response(302)
            self.send_header("Location", f"{server.elsewhere}/landed")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        server.answered.append({name.lower(): value for name, value in self.headers.items()})
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, format: str, *args: Any) -> None:  # pylint: disable=redefined-builtin
        pass


@pytest.fixture(name="servers")
def _servers() -> Iterator[tuple[_Server, _Server]]:
    pair = (_Server(), _Server())
    for server in pair:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield pair
    finally:
        for server in pair:
            server.shutdown()
            server.server_close()


def _ci(url: str) -> None:
    UrllibCiHttp(timeout=5).get_text(f"{url}/job/1/consoleText", headers=authorization_header("secret-token"))


def _gerrit(url: str) -> None:
    HttpGerritRest(url, auth=GerritAuth(user="u", secret="secret-token")).get_account("self")


CLIENTS = pytest.mark.parametrize("fetch", [_ci, _gerrit], ids=["ci", "gerrit"])


@CLIENTS
def test_a_redirect_to_another_origin_is_followed_without_the_credential(
    servers: tuple[_Server, _Server], fetch: Callable[[str], None]
) -> None:
    first, other = servers
    first.elsewhere = other.url
    fetch(first.url)
    [landed] = other.answered
    assert "authorization" not in landed


@CLIENTS
def test_a_redirect_within_the_origin_keeps_the_credential(
    servers: tuple[_Server, _Server], fetch: Callable[[str], None]
) -> None:
    first, _other = servers
    first.elsewhere = first.url
    fetch(first.url)
    [landed] = first.answered
    assert "authorization" in landed


def test_an_origin_is_scheme_host_and_port_with_the_default_port_spelled_out() -> None:
    assert origin("https://CI.example/job") == origin("https://ci.example:443/other")
    assert origin("https://ci.example/") != origin("http://ci.example/")
    assert origin("https://ci.example/") != origin("https://ci.example:8443/")
    assert origin("https://ci.example/") != origin("https://logs.example/")
