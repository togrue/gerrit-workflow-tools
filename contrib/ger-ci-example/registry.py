"""Example ``.ger/ci`` strategy: Jenkins build URL → console deep link.

Copy this directory into a consumer repo as ``.ger/ci/`` (or merge the files), then
adjust ``STRATEGIES`` keys to match ``gerrit.project``.

Prefer Checks-plugin ``url`` fields when present; fall back to parsing change messages.
"""

from __future__ import annotations

import re
from typing import Any

from gerrit_workflow_tools.core.ci_links import CiLink

# Match a Jenkins job build URL that is not already a console/testReport deep link.
_JENKINS_BUILD_RE = re.compile(
    r"(https?://[^\s]+?/job/[^\s]+?/\d+)/?(?:\s|$)",
    re.IGNORECASE,
)


def _to_console(url: str) -> str:
    base = url.rstrip("/")
    if base.endswith("/console") or "/console" in base:
        return base
    return f"{base}/console"


def extract_ci_links(*, project: str, checks: list[dict[str, Any]], messages: list[dict[str, Any]]) -> list[CiLink]:
    """Rewrite failed Checks URLs and Jenkins message URLs to ``…/console``."""
    del project  # keyed via registry; unused inside the transform
    out: list[CiLink] = []
    for row in checks:
        if row.get("state") != "FAILED":
            continue
        raw = row.get("url") or row.get("external_id") or ""
        if not isinstance(raw, str) or not raw.startswith("http"):
            continue
        name = row.get("checker_name") or row.get("name") or "jenkins"
        out.append(CiLink(label=str(name), url=_to_console(raw), source="checks"))
    if out:
        return out
    for msg in messages:
        text = str(msg.get("message") or "")
        match = _JENKINS_BUILD_RE.search(text)
        if match:
            out.append(CiLink(label="jenkins", url=_to_console(match.group(1)), source="message"))
    return out


# Map exact ``gerrit.project`` values to this extractor.
STRATEGIES = {
    "example/project": extract_ci_links,
}
