"""Example ``.ger/ci`` registry: custom CI links + a CI content provider.

Copy this directory into a consumer repo as ``.ger/ci/`` only when the
built-in Jenkins console links / Jenkins content provider are not enough.
Adjust ``STRATEGIES`` keys to match ``gerrit.project``.

Two independent seams live here:

1. ``STRATEGIES`` — keyed by ``gerrit.project``; discover / rewrite **CI links**.
2. ``CONTENT_PROVIDERS`` — matched by URL host/pattern; fetch + parse **CI content**.

See ``docu/gerrit-ci-strategies.md`` and ``docu/spec/ci-content.md``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlparse

from gerrit_workflow_tools.core.ci_content import CiFetchContext, CiFetched
from gerrit_workflow_tools.core.ci_findings import find_ci_findings
from gerrit_workflow_tools.core.ci_http import authorization_header
from gerrit_workflow_tools.core.ci_links import CiLink
from gerrit_workflow_tools.core.ci_strategy import default_extract_ci_links


# --- CI strategy (link discovery / rewrite) ----------------------------------------


def _to_test_report(url: str) -> str:
    """Rewrite a Jenkins console URL to the build's test-report page."""
    base = url.rstrip("/")
    if base.endswith("/testReport"):
        return base
    if base.endswith("/console"):
        base = base[: -len("/console")]
    return f"{base}/testReport"


def extract_ci_links(
    *,
    project: str,
    checks: Sequence[Mapping[str, Any]],
    messages: Sequence[Mapping[str, Any]],
) -> list[CiLink]:
    """Built-in parsing with console URLs rewritten to Jenkins test reports.

    Prefer starting from ``default_extract_ci_links`` so Checks-first filtering
    and lazy ``messages`` fetching stay intact. Replace the body entirely only
    when your bots do not match the built-in Jenkins patterns.
    """
    links = default_extract_ci_links(project=project, checks=checks, messages=messages)
    return [CiLink(label=link.label, url=_to_test_report(link.url), source=link.source) for link in links]


STRATEGIES = {
    # Exact ``gerrit.project`` keys. For many repos sharing one prefix, export
    # ``get_strategy(project) -> callable | None`` instead (see Configuration.md).
    "example/project": extract_ci_links,
}


# --- CI content provider (fetch + parse behind a link) -----------------------------


class BuildsProvider:
    """Fetch a plain-text log from ``builds.example.org`` and extract findings.

    Matched by URL host, not by ``gerrit.project``. Tried before the built-in
    Jenkins provider. Set ``ger.ciCredential.builds.example.org`` when the host
    needs auth (``user:token`` → HTTP Basic; anything else → Bearer).
    """

    id = "builds"

    def matches(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return host == "builds.example.org"

    def cache_key(self, url: str) -> str:
        # Strip query/fragment so ``?view=raw`` and the bare URL share one entry.
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

    def fetch(self, link: CiLink, ctx: CiFetchContext) -> CiFetched:
        headers = authorization_header(ctx.credential)
        log_url = link.url.rstrip("/") + "/log.txt"
        log = ctx.http.get_text(log_url, headers=headers)
        # Reuse the ordered built-in finders (pytest, ruff, mypy, …) when the
        # log looks like a normal CI console; write your own list of CiFinding
        # when the host has a structured API instead.
        findings = find_ci_findings(log.text)
        # ``body`` only when this URL can never change (a finished numbered build).
        # Use ``"none"`` for running builds or floating "latest" URLs.
        return CiFetched(
            url=link.url,
            findings=findings,
            body=log.text,
            cache_verdict="body",
            truncated=log.truncated,
        )


CONTENT_PROVIDERS = [BuildsProvider()]
