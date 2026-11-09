"""Built-in **CI content provider** for Jenkins build URLs.

A replaceable adapter behind the provider seam, not a domain concept: it knows Jenkins URL
shapes (``…/job/<name>[/job/<name>…]/<build>/…``), asks ``api/json`` whether the build is
finished, reads ``consoleText`` and hands the text to the ordered finders.

Cache verdict: a finished build under a numbered URL is immutable → ``body``. A running
build, a floating ``lastBuild``-style URL, or one whose state could not be read → ``none``.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from gerrit_workflow_tools.core.ci_content import CacheVerdict, CiFetchContext, CiFetched
from gerrit_workflow_tools.core.ci_findings import find_ci_findings
from gerrit_workflow_tools.core.ci_http import CiHttpError, authorization_header
from gerrit_workflow_tools.core.ci_links import CiLink


logger = logging.getLogger(__name__)

_BUILD_URL = re.compile(
    r"^(?P<base>https?://[^/?#]+(?:/[^?#]*?)?/job/[^/?#]+(?:/job/[^/?#]+)*/(?P<build>\d+|last\w*Build))(?:[/?#].*)?$"
)


@dataclass(frozen=True)
class JenkinsBuildRef:
    """A Jenkins build named by a URL: ``…/job/X/55`` (numbered) or ``…/job/X/lastBuild`` (floating)."""

    base: str
    build: str

    @property
    def numbered(self) -> bool:
        """Names one build for good, rather than whatever ``lastBuild`` points at now."""
        return self.build.isdigit()


def parse_jenkins_build_url(url: str) -> JenkinsBuildRef | None:
    """The build *url* names, or ``None`` when it is not a Jenkins build URL."""
    m = _BUILD_URL.match(url.strip())
    return JenkinsBuildRef(base=m.group("base"), build=m.group("build")) if m else None


@dataclass(frozen=True)
class _BuildState:
    building: bool | None
    display_name: str | None
    url: str | None


def _read_build_state(ctx: CiFetchContext, ref: JenkinsBuildRef, headers: dict[str, str]) -> _BuildState:
    """``api/json``: whether the build still runs and its name. Unknown fields stay ``None``."""
    try:
        raw = ctx.http.get_text(f"{ref.base}/api/json?tree=building,fullDisplayName,url", headers=headers)
        data = json.loads(raw.text)
    except (CiHttpError, ValueError) as exc:
        logger.debug("Jenkins build state unavailable for %s: %s", ref.base, exc)
        return _BuildState(building=None, display_name=None, url=None)
    if not isinstance(data, dict):
        return _BuildState(building=None, display_name=None, url=None)
    building = data.get("building")
    name = data.get("fullDisplayName")
    url = data.get("url")
    return _BuildState(
        building=building if isinstance(building, bool) else None,
        display_name=name if isinstance(name, str) else None,
        url=url.rstrip("/") if isinstance(url, str) and url.startswith("http") else None,
    )


class JenkinsProvider:
    """Fetch a Jenkins build's console log and extract **CI findings** from it."""

    id = "jenkins"

    def matches(self, url: str) -> bool:
        """Any URL naming a build under ``/job/<name>/``."""
        return parse_jenkins_build_url(url) is not None

    def cache_key(self, url: str) -> str:
        """The build's base URL, so ``console``, ``consoleText`` and ``testReport`` share one entry."""
        ref = parse_jenkins_build_url(url)
        return f"{ref.base}/" if ref else url

    def fetch(self, link: CiLink, ctx: CiFetchContext) -> CiFetched:
        """``api/json`` for state, then ``consoleText``; parse with the built-in finders."""
        ref = parse_jenkins_build_url(link.url)
        if ref is None:
            raise CiHttpError(f"not a Jenkins build URL: {link.url}")
        headers = authorization_header(ctx.credential)
        state = _read_build_state(ctx, ref, headers)
        log = ctx.http.get_text(f"{ref.base}/consoleText", headers=headers)
        verdict: CacheVerdict = "body" if ref.numbered and state.building is False and not log.truncated else "none"
        return CiFetched(
            url=f"{state.url or ref.base}/console",
            findings=find_ci_findings(log.text),
            body=log.text,
            build=state.display_name,
            cache_verdict=verdict,
            truncated=log.truncated,
        )
