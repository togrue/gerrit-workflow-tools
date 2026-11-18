"""Fetching a CI log over HTTP: gzip on the wire, the size cap, progress, and the terminal meter."""

from __future__ import annotations

import gzip
import io
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from gerrit_workflow_tools.core.ci_http import CHUNK_BYTES, UrllibCiHttp
from gerrit_workflow_tools.render.download_meter import QUIET_SECONDS, DownloadMeter, format_bytes


LOG = "".join(f"line {i}: compiling module {i}\n" for i in range(60_000))


class _Console(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # pylint: disable=invalid-name
        body = LOG.encode("utf-8")
        accepts_gzip = "gzip" in (self.headers.get("Accept-Encoding") or "")
        if accepts_gzip and self.path == "/gz":
            body = gzip.compress(body)
        self.send_response(200)
        if accepts_gzip and self.path == "/gz":
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # pylint: disable=redefined-builtin
        pass


@pytest.fixture(name="server")
def _server() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Console)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def test_gzip_is_asked_for_and_inflated(server: str) -> None:
    seen: list[int] = []
    got = UrllibCiHttp(progress=lambda _url, n: seen.append(n)).get_text(f"{server}/gz")
    assert got.text == LOG
    assert not got.truncated
    # Progress counts the bytes on the wire: far fewer than the text, thanks to gzip.
    assert seen and seen[-1] < len(LOG) // 4


def test_plain_body_reports_progress_chunk_by_chunk(server: str) -> None:
    seen: list[int] = []
    got = UrllibCiHttp(progress=lambda _url, n: seen.append(n)).get_text(f"{server}/plain")
    assert got.text == LOG
    assert seen == sorted(seen) and seen[-1] == len(LOG)
    assert len(seen) >= len(LOG) // CHUNK_BYTES


@pytest.mark.parametrize("path", ["/plain", "/gz"])
def test_the_cap_applies_to_the_text_not_the_wire(server: str, path: str) -> None:
    got = UrllibCiHttp().get_text(f"{server}{path}", max_bytes=1000)
    assert got.truncated
    assert got.text == LOG[:1000]


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_meter_stays_quiet_for_a_fast_download_and_off_a_terminal() -> None:
    now = [0.0]
    tty = _Tty()
    meter = DownloadMeter(tty, label="Fetching CI logs", clock=lambda: now[0])
    meter("a", 1000)
    meter.close()
    assert tty.getvalue() == ""
    piped = io.StringIO()
    meter = DownloadMeter(piped, label="Fetching CI logs", clock=lambda: now[0])
    meter("a", 1000)
    now[0] = 10
    meter("a", 2000)
    assert piped.getvalue() == ""


def test_meter_adds_up_concurrent_downloads_and_erases_itself() -> None:
    now = [0.0]
    tty = _Tty()
    meter = DownloadMeter(tty, label="Fetching CI logs", clock=lambda: now[0])
    meter("a", 1_000_000)
    now[0] = QUIET_SECONDS
    meter("b", 2_500_000)
    assert tty.getvalue().endswith("\rFetching CI logs: 3.5 MB from 2 logs")
    meter.close()
    assert tty.getvalue().endswith("\r")
    assert format_bytes(812_000) == "812 kB"
