"""The HTTP seam **CI content providers** fetch through, and its urllib implementation.

Not Gerrit: CI servers have their own hosts and credentials (``ger.ciCredential.<host>``),
so this sits beside, not on, the ``GerritRest`` seam. Tests hand commands a fake ``CiHttp``.
"""

from __future__ import annotations

import base64
import logging
import time
import zlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request

from gerrit_workflow_tools.core.call_trace import record_call
from gerrit_workflow_tools.core.http_connect import urlopen


logger = logging.getLogger(__name__)

DEFAULT_MAX_BYTES = 32 * 1024 * 1024
"""Hard limit on one fetched log; past it the text is cut and marked truncated."""

CHUNK_BYTES = 256 * 1024
"""Bytes read from the socket at a time; progress is reported after each."""

Progress = Callable[[str, int], None]
"""``(url, bytes received so far)``, called as a response body arrives (on the fetching thread)."""


class CiHttpError(Exception):
    """A CI server answered badly or not at all."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class HttpText:
    """A text response: the URL it finally came from, the text, and whether it was cut."""

    url: str
    text: str
    truncated: bool = False


class CiHttp(Protocol):
    """GET text from a CI server."""

    def get_text(self, url: str, *, headers: Mapping[str, str] | None = None, max_bytes: int = ...) -> HttpText:
        """GET *url*; raise :class:`CiHttpError` on HTTP or network failure."""
        ...  # pylint: disable=unnecessary-ellipsis


def authorization_header(credential: str | None) -> dict[str, str]:
    """``Authorization`` for a ``ger.ciCredential.<host>`` value.

    ``user:token`` is HTTP Basic (Jenkins API tokens); anything else is sent as a Bearer token.
    """
    if not credential:
        return {}
    if ":" in credential:
        token = base64.b64encode(credential.encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {token}"}
    return {"Authorization": f"Bearer {credential}"}


class UrllibCiHttp:
    """:class:`CiHttp` over :mod:`urllib` (follows redirects, accepts gzip, reports *progress*)."""

    def __init__(self, *, timeout: float = 120, progress: Progress | None = None) -> None:
        self.timeout = timeout
        self.progress = progress

    def get_text(
        self, url: str, *, headers: Mapping[str, str] | None = None, max_bytes: int = DEFAULT_MAX_BYTES
    ) -> HttpText:
        """GET *url*, keeping at most *max_bytes* of the (decompressed) body."""
        logger.info("GET %s", url)
        req = Request(url, headers={"Accept": "*/*", "Accept-Encoding": "gzip", **(headers or {})}, method="GET")
        t0 = time.perf_counter()
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                gzipped = (resp.headers.get("Content-Encoding") or "").strip().lower() == "gzip"
                raw = self._read(url, resp, max_bytes, gzipped=gzipped)
                final = resp.geturl() or url
        except HTTPError as e:
            raise CiHttpError(f"HTTP {e.code} for {url}: {e.reason}", status=e.code) from e
        except URLError as e:
            raise CiHttpError(f"request to {url} failed: {e.reason!r}") from e
        except (OSError, zlib.error) as e:
            raise CiHttpError(f"request to {url} failed: {e}") from e
        finally:
            record_call("ci", f"GET {urlparse(url).netloc}", time.perf_counter() - t0)
        truncated = len(raw) > max_bytes
        return HttpText(url=final, text=raw[:max_bytes].decode("utf-8", errors="replace"), truncated=truncated)

    def _read(self, url: str, resp: _Readable, max_bytes: int, *, gzipped: bool) -> bytes:
        """The body in chunks: up to *max_bytes* + 1 bytes of text (decompressed), so a cut shows."""
        inflate = zlib.decompressobj(wbits=31) if gzipped else None
        parts: list[bytes] = []
        kept = received = 0
        while kept <= max_bytes:
            chunk = resp.read(CHUNK_BYTES)
            if not chunk:
                break
            received += len(chunk)
            if self.progress is not None:
                self.progress(url, received)
            if inflate is not None:
                # Bounded: a small body that inflates past the limit is not held in full.
                chunk = inflate.decompress(chunk, max_bytes + 1 - kept)
            parts.append(chunk)
            kept += len(chunk)
        if inflate is not None and kept <= max_bytes:
            parts.append(inflate.flush())
        return b"".join(parts)


class _Readable(Protocol):
    def read(self, amt: int, /) -> bytes: ...  # pylint: disable=missing-function-docstring
