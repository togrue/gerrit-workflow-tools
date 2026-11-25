"""CI failure names and transformed build links for ``ger log``.

Strategies live under ``<ger.scriptsDir>/ci/`` (default ``.ger/ci/``) or the
per-host cache directory, and return :class:`CiLink` rows. When no registry
matches, a built-in Jenkins message parser is used (see
``docu/gerrit-ci-strategies.md``). Core prefers Checks-derived links over
message-derived ones.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal


if TYPE_CHECKING:
    from gerrit_workflow_tools.core.gerrit.change_view import ChangeView

CiLinkSource = Literal["checks", "message"]

CiStrategy = Callable[..., list["CiLink"]]


class CiVerdict(str, Enum):
    """What the build behind a CI link reported. Every verdict is a failure for attention and ``ger ci``."""

    FAILED = "failed"
    """The build failed (Jenkins ``Build Failed``, a ``FAILED`` Checks row)."""
    UNSTABLE = "unstable"
    """The build ran but marked itself unstable (Jenkins ``Build Unstable``: failed tests, quality gates)."""


@dataclass(frozen=True)
class CiPatchset:
    """The patchset a commit's **CI links** are about when it is not the commit's own code: the last pushed one."""

    number: int
    commit: str
    """What CI built, and where its findings' lines are."""

    @classmethod
    def current_of(cls, change: ChangeView) -> CiPatchset | None:
        """*change*'s current patchset, when Gerrit sent it."""
        number, commit = change.current_revision_number, change.current_revision
        return cls(number, commit) if number and commit else None


@dataclass(frozen=True)
class CiLink:
    """One useful CI URL after project-specific transform."""

    label: str
    url: str
    source: CiLinkSource
    origin_url: str | None = field(default=None, compare=False)
    """The URL as the CI message or Checks row gave it, when :attr:`url` was derived from it (a
    Jenkins build URL turned into its console page). What to quote when naming the build."""
    verdict: CiVerdict = CiVerdict.FAILED
    """What the build reported; a custom strategy that does not say is taken as ``failed``."""

    @property
    def build_url(self) -> str:
        """The build's URL as CI reported it: :attr:`origin_url`, else :attr:`url`."""
        return self.origin_url or self.url


@dataclass(frozen=True)
class CiPipeline:
    """One Checks-plugin row for verbose ``ger log`` CI display."""

    label: str
    state: str
    """The Checks state (``FAILED``, ``SUCCESSFUL``, ``RUNNING``, …); an unstable build is ``FAILED``."""
    url: str | None = None
    outdated: bool = False
    verdict: CiVerdict | None = None
    """For a failed build: which failure (:class:`CiVerdict`); ``None`` when the state says no more."""


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
