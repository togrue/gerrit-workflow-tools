"""Fetching **CI content** for **CI links**: provider match, credentials, cache policy, body shape.

The one entry point both ``ger ci`` and ``ger show --ci`` use. It never raises for a link it
could not fetch: the failure is recorded on that result's provenance, and the command decides
whether that is fatal (``ger ci``) or a note in its output (``ger show``).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
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
from gerrit_workflow_tools.core.ci_http import CiHttp, UrllibCiHttp
from gerrit_workflow_tools.core.ci_links import CiLink
from gerrit_workflow_tools.core.ci_paths import resolve_finding_paths, tracked_paths
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.rest import parallel_map


logger = logging.getLogger(__name__)

TAIL_LINES = 60
"""Log lines a ``tail`` body keeps (``ger ci -v``)."""


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
    ) -> CiContentService:
        """Providers from ``.ger/ci/`` plus built-ins; the store beside *web_base*'s Gerrit cache."""
        from gerrit_workflow_tools.core.ci_strategy import load_ci_content_providers

        base = web_base or settings.gerrit_web_url
        return cls(
            providers=load_ci_content_providers(cwd, settings=settings, web_base=base),
            http=http if http is not None else UrllibCiHttp(),
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
        return parallel_map(partial(self.fetch_one, link, body=body, commit=commit) for link in links)

    def fetch_one(self, link: CiLink, *, body: BodyMode = "none", commit: str | None = None) -> CiContent:
        """**CI content** for *link*; a failure is recorded on the provenance, never raised.

        Paths are resolved here, after the store: what is kept is what the log said, and one
        build's findings can be read against whichever commit asks.
        """
        content = self._fetch_logged(link, body)
        if commit is None or self.cwd is None or not content.findings:
            return content
        return replace(content, findings=resolve_finding_paths(content.findings, tracked_paths(self.cwd, commit)))

    def _fetch_logged(self, link: CiLink, body: BodyMode) -> CiContent:
        provider = self.provider_for(link.url)
        if provider is None:
            host = urlparse(link.url).hostname or link.url
            return _failed(link, None, f"no CI content provider matches {host}")
        provider_id = str(getattr(provider, "id", type(provider).__name__))
        key = f"{provider_id}:{_cache_key(provider, link.url)}"

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
