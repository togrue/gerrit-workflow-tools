"""CI failure names and transformed build links for ``ger log``.

Strategies live under ``<gerrit.scriptsDir>/ci/`` (default ``.ger/ci/``) or the
per-host cache directory, and return :class:`CiLink` rows. When no registry
matches, a built-in Jenkins message parser is used (see
``docu/gerrit-ci-strategies.md``). Core prefers Checks-derived links over
message-derived ones.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal


CiLinkSource = Literal["checks", "message"]

CiStrategy = Callable[..., list["CiLink"]]


@dataclass(frozen=True)
class CiLink:
    """One useful CI URL after project-specific transform."""

    label: str
    url: str
    source: CiLinkSource


@dataclass(frozen=True)
class CiPipeline:
    """One Checks-plugin row for verbose ``ger log`` CI display."""

    label: str
    state: str
    url: str | None = None


def failed_check_names(checks: Sequence[Mapping[str, Any]]) -> list[str]:
    """Return display names for Checks rows in ``FAILED`` state."""

    failed: list[str] = []
    for check in checks:
        if check.get("state") != "FAILED":
            continue
        name = check.get("checker_name") or check.get("name") or ""
        if name:
            failed.append(str(name))
    return failed


def ci_pipelines_from_checks(
    checks: Sequence[Mapping[str, Any]],
    links: Sequence[CiLink],
) -> list[CiPipeline]:
    """Build display pipelines from Checks rows, overlaying strategy URLs by label."""

    link_by_label = {link.label: link.url for link in links}
    pipelines: list[CiPipeline] = []
    for check in checks:
        name = check.get("checker_name") or check.get("name") or ""
        if not name:
            continue
        label = str(name)
        raw_url = check.get("url") or check.get("external_id") or ""
        row_url = str(raw_url) if isinstance(raw_url, str) and raw_url.startswith("http") else None
        pipelines.append(
            CiPipeline(
                label=label,
                state=str(check.get("state") or ""),
                url=link_by_label.get(label) or row_url,
            )
        )
    return pipelines


def prefer_checks_links(links: Sequence[CiLink]) -> list[CiLink]:
    """Keep Checks links when any exist; otherwise keep message links."""

    rows = list(links)
    if any(link.source == "checks" for link in rows):
        return [link for link in rows if link.source == "checks"]
    return rows


def apply_ci_strategy(
    strategy: CiStrategy | None,
    *,
    project: str,
    checks: Sequence[Mapping[str, Any]],
    messages: Sequence[Mapping[str, Any]],
) -> list[CiLink]:
    """Run *strategy* and apply Checks-first filtering.

    Returns an empty list when *strategy* is ``None``.
    """

    if strategy is None:
        return []
    raw = strategy(project=project, checks=list(checks), messages=messages)
    return prefer_checks_links(raw)
