"""**CI content**: what sits behind one **CI link**, and the **CI content provider** seam.

Layering (``docu/spec/ci-content.md``): a **CI strategy** discovers **CI links** (cheap, in
the overlay); a **CI content provider** matched by URL host/pattern fetches and parses what a
link points at into one :class:`CiContent` — provenance, **CI findings**, optional body.
Views (``ger ci``, ``ger show --ci``) are projections of that one result.

This module holds only the shapes and the provider contract. Fetch orchestration, credentials
and cache policy live in :mod:`~gerrit_workflow_tools.core.ci_content_service`; the built-in
Jenkins provider in :mod:`~gerrit_workflow_tools.core.ci_jenkins`.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from gerrit_workflow_tools.core.ci_http import CiHttp
from gerrit_workflow_tools.core.ci_links import CiLink


CacheVerdict = Literal["none", "findings", "body"]
"""Whether a fetched result is safe to reuse: not at all, its findings, or findings and body."""

BodyMode = Literal["none", "tail", "full"]
"""How much log text a caller wants back: none (``ger show``), a tail (``ger ci -v``), all (``--full``)."""

CACHE_VERDICTS: tuple[CacheVerdict, ...] = ("none", "findings", "body")


class CiContentError(Exception):
    """A **CI link**'s content could not be fetched or parsed where the command required it."""


def finding_fingerprint(tool: str, kind: str, path: str | None, message: str) -> str:
    """Stable id of a finding that survives line shifts: tool, kind, path and message only."""
    raw = "\0".join((tool, kind, path or "", " ".join(message.split())))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class CiFinding:
    """One normalized failure or diagnostic extracted from **CI content**."""

    tool: str
    """Which tool produced it: ``pytest``, ``ruff``, ``mypy``, ``cc``, ``clang-format``, ``ld``, ``generic``."""
    kind: str
    """What it is for that tool, e.g. ``failed`` (pytest), ``lint`` (ruff), ``error`` / ``warning``."""
    message: str
    path: str | None = None
    line: int | None = None
    column: int | None = None
    detail: str | None = None
    """A short excerpt (e.g. pytest's ``E`` lines), when the tool gives one."""
    fingerprint: str = ""

    def __post_init__(self) -> None:
        if not self.fingerprint:
            object.__setattr__(self, "fingerprint", finding_fingerprint(self.tool, self.kind, self.path, self.message))

    @property
    def location(self) -> str:
        """``path:line:column`` as far as known, or ``""``."""
        if not self.path:
            return ""
        parts = [self.path, *(str(n) for n in (self.line, self.column) if n is not None)]
        return ":".join(parts) if self.line is not None else self.path

    def to_dict(self) -> dict[str, Any]:
        """The JSON shape of a finding: the agent-facing contract, also what the store keeps."""
        return {
            "tool": self.tool,
            "kind": self.kind,
            "path": self.path,
            "line": self.line,
            "column": self.column,
            "message": self.message,
            "detail": self.detail,
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> CiFinding:
        """Rebuild a finding from :meth:`to_dict` output."""

        def _int(key: str) -> int | None:
            value = raw.get(key)
            return value if isinstance(value, int) else None

        return cls(
            tool=str(raw.get("tool") or "generic"),
            kind=str(raw.get("kind") or "error"),
            message=str(raw.get("message") or ""),
            path=raw.get("path") if isinstance(raw.get("path"), str) else None,
            line=_int("line"),
            column=_int("column"),
            detail=raw.get("detail") if isinstance(raw.get("detail"), str) else None,
            fingerprint=str(raw.get("fingerprint") or ""),
        )


@dataclass(frozen=True)
class CiFetched:
    """What a provider returns for one link; core turns it into :class:`CiContent`."""

    url: str
    """The final URL a human would open (e.g. a Jenkins ``…/<n>/console`` page)."""
    findings: list[CiFinding]
    body: str | None = None
    """The full log text the findings were parsed from, when the provider has one."""
    build: str | None = None
    """Build identity when known, e.g. ``Widget #55``."""
    cache_verdict: CacheVerdict = "none"
    truncated: bool = False
    """The fetched log was cut at a size limit, so findings may be incomplete."""


@dataclass(frozen=True)
class CiProvenance:
    """Where a :class:`CiContent` came from and how far to trust it."""

    url: str
    provider: str | None
    fetched_at: str | None = None
    build: str | None = None
    truncated: bool = False
    cache_verdict: CacheVerdict = "none"
    cached: bool = False
    """Served from the CI-content store rather than fetched now."""
    error: str | None = None
    """Why the fetch or parse failed; ``None`` on success."""

    def to_dict(self) -> dict[str, Any]:
        """The JSON shape of provenance."""
        return {
            "url": self.url,
            "provider": self.provider,
            "build": self.build,
            "fetched_at": self.fetched_at,
            "truncated": self.truncated,
            "cache_verdict": self.cache_verdict,
            "cached": self.cached,
            "error": self.error,
        }


@dataclass(frozen=True)
class CiContent:
    """**CI content** for one **CI link**: provenance, **CI findings**, optional body."""

    link: CiLink
    provenance: CiProvenance
    findings: list[CiFinding] = field(default_factory=list)
    body: str | None = None
    """Log text as the caller asked for it (see :data:`BodyMode`); ``None`` when not asked or not had."""
    body_omitted_lines: int = 0
    """Leading log lines left out of :attr:`body` (a ``tail`` view)."""

    @property
    def failed(self) -> bool:
        """The content could not be fetched or parsed."""
        return self.provenance.error is not None


def introduced_fingerprints(per_commit: Sequence[Sequence[CiContent] | None]) -> list[frozenset[str] | None]:
    """**Introduced findings** for each commit of a chain, base first: fingerprints its parent lacks.

    An entry is the **CI content** of one commit's failed **CI links** (``[]``: CI is not
    failing), or ``None`` when its CI state is not known (not on Gerrit, or changed since CI
    ran). The base's parent is the target branch, taken as clean. The answer is ``None``
    where it cannot be told: the commit's own state is unknown, or its parent's is, or a
    parent link could not be fetched.
    """
    introduced: list[frozenset[str] | None] = []
    parent: frozenset[str] | None = frozenset()
    for contents in per_commit:
        if contents is None:
            introduced.append(None)
            parent = None
            continue
        own = frozenset(f.fingerprint for content in contents if not content.failed for f in content.findings)
        introduced.append(own - parent if parent is not None else None)
        parent = None if any(content.failed for content in contents) else own
    return introduced


@dataclass(frozen=True)
class CiFetchContext:
    """What core hands a provider for one fetch: the HTTP seam and the host's credential."""

    http: CiHttp
    credential: str | None = None
    """``ger.ciCredential.<host>`` for the link's host, if set."""


class CiContentProvider(Protocol):
    """Matches **CI link** URLs by host/pattern and fetches + parses what is behind them.

    Custom providers go in ``CONTENT_PROVIDERS`` of ``.ger/ci/registry.py``; they are tried
    before the built-in Jenkins provider. ``fetch`` may raise: core records the error on
    the result's provenance instead of failing the command.
    """

    id: str

    def matches(self, url: str) -> bool:
        """Whether this provider knows how to fetch *url*."""
        ...  # pylint: disable=unnecessary-ellipsis

    def cache_key(self, url: str) -> str:
        """A stable key for what *url* names (usually a canonical build URL)."""
        ...  # pylint: disable=unnecessary-ellipsis

    def fetch(self, link: CiLink, ctx: CiFetchContext) -> CiFetched:
        """Fetch and parse *link*; return findings, body and a cache verdict."""
        ...  # pylint: disable=unnecessary-ellipsis
