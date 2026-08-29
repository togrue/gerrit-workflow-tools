"""Example ``.ger/ci`` strategy: Jenkins URLs → test report deep links.

Copy this directory into a consumer repo as ``.ger/ci/`` only when the built-in
console links are not what you want. Adjust ``STRATEGIES`` keys to match
``gerrit.project``. See ``docu/gerrit-ci-strategies.md``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from gerrit_workflow_tools.core.ci_links import CiLink
from gerrit_workflow_tools.core.ci_strategy import default_extract_ci_links


def _to_test_report(url: str) -> str:
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
    """Built-in parsing with console URLs rewritten to Jenkins test reports."""

    links = default_extract_ci_links(project=project, checks=checks, messages=messages)
    return [
        CiLink(label=link.label, url=_to_test_report(link.url), source=link.source) for link in links
    ]


STRATEGIES = {
    "example/project": extract_ci_links,
}
