"""CI failure names and transformed build links for ``ger log``.

Strategies live in the consumer repo under ``.ger/ci/`` and return :class:`CiLink`
rows. Core prefers Checks-derived links over message-derived ones.
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
    raw = strategy(project=project, checks=list(checks), messages=list(messages))
    return prefer_checks_links(raw)
