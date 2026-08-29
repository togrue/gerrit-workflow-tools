"""Example ``.ger/ci`` strategy: Jenkins build URL → console deep link.

Copy this directory into a consumer repo as ``.ger/ci/`` (or merge the files), then
adjust ``STRATEGIES`` keys to match ``gerrit.project``.

**ger** already parses common Jenkins Gerrit trigger messages by default (see
``docu/gerrit-ci-strategies.md``). Use this example when you need project-specific
URL transforms or non-Jenkins CI.
"""

from __future__ import annotations

from typing import Any

from gerrit_workflow_tools.core.ci_links import CiLink
from gerrit_workflow_tools.core.gerrit_message_parsing import builtin_extract_ci_links


def extract_ci_links(*, project: str, checks: list[dict[str, Any]], messages: list[dict[str, Any]]) -> list[CiLink]:
    """Delegate to the built-in parser, or replace with custom logic."""
    return builtin_extract_ci_links(project=project, checks=checks, messages=messages)


# Map exact ``gerrit.project`` values to this extractor.
STRATEGIES = {
    "example/project": extract_ci_links,
}
