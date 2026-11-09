"""The CI-content store: fetched **CI content** kept apart from the Gerrit ChangeInfo cache.

Policy split (``docu/spec/ci-content.md`` § Caching): the provider's **cache verdict** says
whether a result is *safe* to reuse; this store decides whether we can *afford* to keep it.
Entries are only ever written for immutable content (verdict ``findings`` or ``body``), so a
hit is valid by construction — there is no staleness to guess at. Bodies over
:attr:`CiContentStore.max_body_bytes` are dropped (findings kept); the directory is held under
:attr:`CiContentStore.budget_bytes` by evicting the least recently used entries.

One JSON file per entry under ``<cache dir>/<host>/ci_content/``.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from gerrit_workflow_tools.core.ci_content import CACHE_VERDICTS, CacheVerdict, CiFinding
from gerrit_workflow_tools.core.gerrit.paths import gerrit_cache_dir


logger = logging.getLogger(__name__)

DEFAULT_BUDGET_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_BODY_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class StoredCiContent:
    """One store entry: what a fetch produced, as far as the verdict allowed keeping it."""

    provider: str
    url: str
    fetched_at: str
    findings: list[CiFinding]
    build: str | None = None
    truncated: bool = False
    cache_verdict: CacheVerdict = "findings"
    body: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON for the entry file."""
        return {
            "provider": self.provider,
            "url": self.url,
            "fetched_at": self.fetched_at,
            "build": self.build,
            "truncated": self.truncated,
            "cache_verdict": self.cache_verdict,
            "findings": [f.to_dict() for f in self.findings],
            "body": self.body,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> StoredCiContent:
        """Rebuild an entry from :meth:`to_dict` output; raise ``ValueError`` when malformed."""
        verdict = raw.get("cache_verdict")
        findings = raw.get("findings")
        if verdict not in CACHE_VERDICTS or not isinstance(findings, list):
            raise ValueError("malformed CI content entry")
        body = raw.get("body")
        build = raw.get("build")
        return cls(
            provider=str(raw.get("provider") or ""),
            url=str(raw.get("url") or ""),
            fetched_at=str(raw.get("fetched_at") or ""),
            findings=[CiFinding.from_dict(f) for f in findings if isinstance(f, dict)],
            build=build if isinstance(build, str) else None,
            truncated=bool(raw.get("truncated")),
            cache_verdict=verdict,
            body=body if isinstance(body, str) else None,
        )


class CiContentStore:
    """Size-bounded, least-recently-used store of immutable **CI content**."""

    def __init__(
        self,
        root: Path,
        *,
        budget_bytes: int = DEFAULT_BUDGET_BYTES,
        max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    ) -> None:
        self.root = root
        self.budget_bytes = budget_bytes
        self.max_body_bytes = max_body_bytes

    @classmethod
    def for_web_base(cls, web_base: str) -> CiContentStore:
        """The store beside the Gerrit cache of *web_base*'s host."""
        return cls(gerrit_cache_dir(web_base) / "ci_content")

    def _path(self, key: str) -> Path:
        return self.root / f"{hashlib.sha256(key.encode('utf-8')).hexdigest()[:40]}.json"

    def get(self, key: str) -> StoredCiContent | None:
        """The entry for *key*, marking it recently used; ``None`` on a miss or a broken file."""
        path = self._path(key)
        try:
            entry = StoredCiContent.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            logger.debug("dropping unreadable CI content entry %s: %s", path, exc)
            with contextlib.suppress(OSError):
                path.unlink()
            return None
        with contextlib.suppress(OSError):
            os.utime(path)
        return entry

    def put(self, key: str, entry: StoredCiContent) -> bool:
        """Keep *entry* as far as its verdict and the size caps allow; return whether anything was kept."""
        if entry.cache_verdict == "none":
            return False
        body = entry.body
        if entry.cache_verdict != "body" or (body is not None and len(body.encode("utf-8")) > self.max_body_bytes):
            body = None
        kept = replace(entry, body=body)
        path = self._path(key)
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(kept.to_dict(), separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, path)
        except OSError as exc:
            logger.debug("could not store CI content %s: %s", path, exc)
            return False
        self._evict()
        return True

    def _evict(self) -> None:
        """Delete least recently used entries until the store fits :attr:`budget_bytes`."""
        try:
            entries = [(p.stat(), p) for p in self.root.glob("*.json")]
        except OSError:
            return
        total = sum(st.st_size for st, _ in entries)
        for st, path in sorted(entries, key=lambda e: e[0].st_mtime):
            if total <= self.budget_bytes:
                break
            with contextlib.suppress(OSError):
                path.unlink()
                total -= st.st_size

    def count(self) -> int:
        """How many entries the store holds."""
        return sum(1 for _ in self.root.glob("*.json"))

    def clear(self) -> int:
        """Delete every entry; return how many were removed."""
        removed = 0
        for path in self.root.glob("*.json"):
            with contextlib.suppress(OSError):
                path.unlink()
                removed += 1
        return removed
