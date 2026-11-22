"""Fetching **CI content** for **CI links**: provider match, credentials, cache policy, body shape.

The one entry point both ``ger ci`` and ``ger show --ci`` use. It never raises for a link it
could not fetch: the failure is recorded on that result's provenance, and the command decides
whether that is fatal (``ger ci``) or a note in its output (``ger show``).
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from types import ModuleType
from urllib.parse import urlparse

from gerrit_workflow_tools.core.ci_content import (
    BodyMode,
    CiContent,
    CiContentProvider,
    CiFetchContext,
    CiFetched,
    CiProvenance,
)
from gerrit_workflow_tools.core.ci_content_store import CiContentStore, StoredCiContent
from gerrit_workflow_tools.core.ci_http import CiHttp, Progress, UrllibCiHttp
from gerrit_workflow_tools.core.ci_links import CiLink
from gerrit_workflow_tools.core.ci_paths import resolve_finding_paths, tracked_paths
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.ger_registry import SOURCE_DIGEST, source_digest
from gerrit_workflow_tools.core.gerrit.rest import parallel_map
from gerrit_workflow_tools.core.git_state import repo_toplevel


logger = logging.getLogger(__name__)

TAIL_LINES = 60
"""Log lines a ``tail`` body keeps (``ger ci -v``)."""

_GER_CI_DIGEST = source_digest(Path(__file__).resolve().parent.glob("ci_*.py"))
"""ger's own CI code (``core/ci_*.py``), taken at import: the code this process runs."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def shape_body(body: str | None, mode: BodyMode) -> tuple[str | None, int]:
    """``(body, omitted_leading_lines)`` for what the caller asked: nothing, a tail, or all."""
    if body is None or mode == "none":
        return None, 0
    if mode == "full":
        return body, 0
    lines = body.splitlines()
    omitted = max(0, len(lines) - TAIL_LINES)
    return "\n".join(lines[omitted:]), omitted


@dataclass
class CiContentService:
    """Fetches **CI content** through the matching provider, reusing the store when allowed."""

    providers: Sequence[CiContentProvider]
    http: CiHttp
    settings: Settings
    store: CiContentStore | None = None
    refresh: bool = False
    cwd: Path | str | None = None
    """The repository whose commits finding paths are resolved against; ``None``: paths stay as logged."""

    @classmethod
    def from_settings(
        cls,
        cwd: Path | str | None,
        settings: Settings,
        *,
        web_base: str | None = None,
        http: CiHttp | None = None,
        refresh: bool = False,
        progress: Progress | None = None,
    ) -> CiContentService:
        """Providers from ``.ger/ci/`` plus built-ins; the store beside *web_base*'s Gerrit cache.

        *progress* hears the bytes of each download as they arrive (unless *http* is given).
        """
        from gerrit_workflow_tools.core.ci_strategy import load_ci_content_providers

        base = web_base or settings.gerrit_web_url
        return cls(
            providers=load_ci_content_providers(repo_toplevel(cwd), settings=settings, web_base=base),
            http=http if http is not None else UrllibCiHttp(progress=progress),
            settings=settings,
            store=CiContentStore.for_web_base(base) if base else None,
            refresh=refresh,
            cwd=cwd,
        )

    def provider_for(self, url: str) -> CiContentProvider | None:
        """The first provider that claims *url*."""
        for provider in self.providers:
            try:
                if provider.matches(url):
                    return provider
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning("CI content provider %r failed to match %s: %s", provider, url, exc)
        return None

    def fetch(self, links: Sequence[CiLink], *, body: BodyMode = "none", commit: str | None = None) -> list[CiContent]:
        """One :class:`CiContent` per link, in order, fetched in parallel.

        *commit* is the commit the links belong to: finding paths are resolved to its files.
        """
        return self.fetch_each([(links, commit)], body=body)[0]

    def fetch_each(
        self, groups: Sequence[tuple[Sequence[CiLink], str | None]], *, body: BodyMode = "none"
    ) -> list[list[CiContent]]:
        """:meth:`fetch` for each ``(links, commit)`` group, every link of every group in parallel.

        A stack's commits each have their own builds: fetching them one commit after the other
        would make the slowest log of each commit add up. Commits that name the same link share
        one download.
        """
        pairs = [(link, commit) for links, commit in groups for link in links]
        unique = list(dict.fromkeys(link for link, _commit in pairs))
        by_link = dict(
            zip(unique, parallel_map(partial(self._fetch_logged, link, body) for link in unique), strict=True)
        )
        flat = iter(self._resolved(by_link[link], commit) for link, commit in pairs)
        return [[next(flat) for _ in links] for links, _commit in groups]

    def fetch_one(self, link: CiLink, *, body: BodyMode = "none", commit: str | None = None) -> CiContent:
        """**CI content** for *link*; a failure is recorded on the provenance, never raised.

        Paths are resolved here, after the store: what is kept is what the log said, and one
        build's findings can be read against whichever commit asks.
        """
        return self._resolved(self._fetch_logged(link, body), commit)

    def _resolved(self, content: CiContent, commit: str | None) -> CiContent:
        if commit is None or self.cwd is None or not content.findings:
            return content
        return replace(content, findings=resolve_finding_paths(content.findings, tracked_paths(self.cwd, commit)))

    def _fetch_logged(self, link: CiLink, body: BodyMode) -> CiContent:
        provider = self.provider_for(link.url)
        if provider is None:
            host = urlparse(link.url).hostname or link.url
            return _failed(link, None, f"no CI content provider matches {host}")
        provider_id = str(getattr(provider, "id", type(provider).__name__))
        key = f"{provider_id}:{parser_fingerprint(provider)}:{_cache_key(provider, link.url)}"

        stored = None if self.refresh or self.store is None else self.store.get(key)
        if stored is not None and (body == "none" or stored.body is not None):
            return _from_stored(link, stored, body)

        ctx = CiFetchContext(http=self.http, credential=self.settings.ci_credential(urlparse(link.url).hostname or ""))
        try:
            fetched = provider.fetch(link, ctx)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.debug("CI content fetch failed for %s: %s", link.url, exc)
            return _failed(link, provider_id, str(exc) or type(exc).__name__)
        fetched_at = _now()
        if self.store is not None:
            self.store.put(key, _to_stored(provider_id, fetched, fetched_at))
        text, omitted = shape_body(fetched.body, body)
        return CiContent(
            link=link,
            provenance=CiProvenance(
                url=fetched.url,
                provider=provider_id,
                fetched_at=fetched_at,
                build=fetched.build,
                truncated=fetched.truncated,
                cache_verdict=fetched.cache_verdict,
            ),
            findings=list(fetched.findings),
            body=text,
            body_omitted_lines=omitted,
        )


def parser_fingerprint(provider: CiContentProvider) -> str:
    """What made a stored entry's findings: ger's CI code and the provider's registry, as loaded.

    Part of the store key, so findings parsed by other code are a miss, not served as current.
    A provider from ``.ger/ci/`` carries the digest its registry directory had when it loaded;
    one from elsewhere (ger's own Jenkins provider, a test double) adds nothing to ger's.
    """
    if isinstance(provider, ModuleType):
        namespace = vars(provider)
    else:
        namespace = getattr(getattr(type(provider), "fetch", None), "__globals__", None) or {}
    loaded = namespace.get(SOURCE_DIGEST)
    combined = f"{_GER_CI_DIGEST}:{loaded if isinstance(loaded, str) else ''}"
    return hashlib.sha256(combined.encode("ascii")).hexdigest()[:16]


def _cache_key(provider: CiContentProvider, url: str) -> str:
    key_of = getattr(provider, "cache_key", None)
    return str(key_of(url)) if callable(key_of) else url


def _failed(link: CiLink, provider: str | None, error: str) -> CiContent:
    return CiContent(link=link, provenance=CiProvenance(url=link.url, provider=provider, error=error))


def _to_stored(provider: str, fetched: CiFetched, fetched_at: str) -> StoredCiContent:
    return StoredCiContent(
        provider=provider,
        url=fetched.url,
        fetched_at=fetched_at,
        findings=list(fetched.findings),
        build=fetched.build,
        truncated=fetched.truncated,
        cache_verdict=fetched.cache_verdict,
        body=fetched.body,
    )


def _from_stored(link: CiLink, stored: StoredCiContent, body: BodyMode) -> CiContent:
    text, omitted = shape_body(stored.body, body)
    return CiContent(
        link=link,
        provenance=CiProvenance(
            url=stored.url,
            provider=stored.provider,
            fetched_at=stored.fetched_at,
            build=stored.build,
            truncated=stored.truncated,
            cache_verdict=stored.cache_verdict,
            cached=True,
        ),
        findings=list(stored.findings),
        body=text,
        body_omitted_lines=omitted,
    )
