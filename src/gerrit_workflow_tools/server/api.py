"""The ``ger serve`` API: which endpoint answers which call, and the per-request context it runs in.

Transport-free, so tests drive it without a socket: :meth:`GerApi.handle` takes a method,
a path, query parameters and a JSON body, and returns a JSON value or raises.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from gerrit_workflow_tools.core.ci_http import CiHttp
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.rest import GerritRest
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.git_run import clear_git_cache
from gerrit_workflow_tools.server import actions, views
from gerrit_workflow_tools.server.request import Context, NoRoute, Request


PREFIX = f"/api/v{views.API_VERSION}"

Endpoint = Callable[[Context, Request], dict[str, Any]]

ROUTES: tuple[tuple[str, str, Endpoint], ...] = (
    ("GET", "/health", views.health),
    ("GET", "/overview", views.overview),
    ("GET", "/stack", views.stack),
    ("GET", "/chains/([^/]+)", views.chain),
    ("GET", "/show", views.show),
    ("GET", "/diff", views.diff),
    ("GET", "/file", views.file),
    ("GET", "/review", views.review),
    ("POST", "/review/open", actions.review_open),
    ("POST", "/review/done", actions.review_done),
    ("POST", "/comments/reply", actions.reply),
    ("POST", "/stack/reorder", actions.reorder),
)

_REFRESH_ENV = "GER_CACHE_REFRESH"


@contextmanager
def _cache_refresh(on: bool) -> Iterator[None]:
    """Run one request the way ``ger --refresh`` runs a command."""
    if not on:
        yield
        return
    before = os.environ.get(_REFRESH_ENV)
    os.environ[_REFRESH_ENV] = "1"
    try:
        yield
    finally:
        if before is None:
            os.environ.pop(_REFRESH_ENV, None)
        else:
            os.environ[_REFRESH_ENV] = before


class GerApi:
    """The endpoints over one repository.

    Not safe for concurrent calls: core memoises git answers per process, and a request
    may set the refresh environment. The HTTP layer serialises requests.
    """

    def __init__(self, cwd: Path | str, *, gerrit: GerritRest | None = None, ci_http: CiHttp | None = None) -> None:
        self.cwd = Path(cwd)
        self._gerrit = gerrit
        self._ci_http = ci_http

    def _context(self) -> Context:
        """A fresh view per request: the repository may have changed since the last one."""
        clear_git_cache()
        settings = Settings.from_cwd(self.cwd)
        service = GerritService.from_cwd(self.cwd, settings=settings, rest=self._gerrit)
        return Context(cwd=self.cwd, settings=settings, service=service, ci_http=self._ci_http)

    def handle(
        self,
        method: str,
        path: str,
        query: Mapping[str, list[str]] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Answer one call. Raises :class:`NoRoute` for an unknown one; core's errors pass through."""
        if not path.startswith(PREFIX + "/"):
            raise NoRoute(f"no such endpoint: {path} (the API lives under {PREFIX}/)")
        tail = path[len(PREFIX) :].rstrip("/")
        for route_method, pattern, endpoint in ROUTES:
            match = re.fullmatch(pattern, tail)
            if match is None or route_method != method:
                continue
            request = Request(query=query or {}, body=body or {}, path_args=match.groups())
            with _cache_refresh(request.flag("refresh")):
                return endpoint(self._context(), request)
        raise NoRoute(f"no such endpoint: {method} {path}")
