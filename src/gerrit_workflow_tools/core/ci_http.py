"""The HTTP seam **CI content providers** fetch through, and its urllib implementation.

Not Gerrit: CI servers have their own hosts and credentials (``ger.ciCredential.<host>``),
so this sits beside, not on, the ``GerritRest`` seam. Tests hand commands a fake ``CiHttp``.
"""

from __future__ import annotations

import base64
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from gerrit_workflow_tools.core.call_trace import record_call


logger = logging.getLogger(__name__)

DEFAULT_MAX_BYTES = 32 * 1024 * 1024
"""Hard limit on one fetched log; past it the text is cut and marked truncated."""


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
    """:class:`CiHttp` over :mod:`urllib` (follows redirects)."""

    def __init__(self, *, timeout: float = 120) -> None:
        self.timeout = timeout

    def get_text(
        self, url: str, *, headers: Mapping[str, str] | None = None, max_bytes: int = DEFAULT_MAX_BYTES
    ) -> HttpText:
        """GET *url*, reading at most *max_bytes*."""
        logger.info("GET %s", url)
        req = Request(url, headers={"Accept": "*/*", **(headers or {})}, method="GET")
        t0 = time.perf_counter()
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read(max_bytes + 1)
                final = resp.geturl() or url
        except HTTPError as e:
            raise CiHttpError(f"HTTP {e.code} for {url}: {e.reason}", status=e.code) from e
        except URLError as e:
            raise CiHttpError(f"request to {url} failed: {e.reason!r}") from e
        except OSError as e:
            raise CiHttpError(f"request to {url} failed: {e}") from e
        finally:
            record_call("ci", f"GET {urlparse(url).netloc}", time.perf_counter() - t0)
        truncated = len(raw) > max_bytes
        return HttpText(url=final, text=raw[:max_bytes].decode("utf-8", errors="replace"), truncated=truncated)
