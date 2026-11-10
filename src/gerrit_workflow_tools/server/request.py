"""What an endpoint is handed: the parsed request, and the per-request view of the repository."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gerrit_workflow_tools.core.ci_content_service import CiContentService
from gerrit_workflow_tools.core.ci_http import CiHttp
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.service import GerritService


class BadRequest(ValueError):
    """The request is malformed: a missing or ill-typed parameter (HTTP 400)."""


class NoRoute(LookupError):
    """No endpoint answers this method and path (HTTP 404)."""


_TRUE = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True)
class Request:
    """Query parameters, JSON body and path captures of one call."""

    query: Mapping[str, list[str]] = field(default_factory=dict)
    body: Mapping[str, Any] = field(default_factory=dict)
    path_args: tuple[str, ...] = ()

    def many(self, name: str) -> list[str]:
        """Every value of query parameter *name*."""
        return [value for value in self.query.get(name, []) if value]

    def one(self, name: str, default: str | None = None) -> str | None:
        """The last value of query parameter *name*."""
        values = self.many(name)
        return values[-1] if values else default

    def flag(self, name: str) -> bool:
        """Whether query parameter *name* is switched on."""
        return (self.one(name) or "").lower() in _TRUE

    def number(self, name: str) -> int | None:
        """Query parameter *name* as a non-negative number, or ``None`` when absent."""
        raw = self.one(name)
        if raw is None:
            return None
        if not raw.isdigit():
            raise BadRequest(f"{name} must be a non-negative number, not {raw!r}")
        return int(raw)

    def body_field(self, name: str, kind: type | tuple[type, ...], *, required: bool = False) -> Any:
        """Body field *name*, checked to be of *kind*; ``None`` when absent and not *required*."""
        value = self.body.get(name)
        if value is None:
            if required:
                raise BadRequest(f"missing field {name!r}")
            return None
        # bool is an int in Python; a JSON true is never a number here.
        if not isinstance(value, kind) or (isinstance(value, bool) and kind is not bool):
            raise BadRequest(f"field {name!r} has the wrong type")
        return value


def safe_rev(value: str | None, name: str = "rev") -> str:
    """A changeish or revision from the client; never something git would read as an option."""
    text = (value or "").strip()
    if not text or text.startswith("-") or any(ch in text for ch in "\0\n\r"):
        raise BadRequest(f"{name} is missing or not a revision")
    return text


@dataclass(frozen=True)
class Context:
    """One request's view: the repository, a fresh **Settings** snapshot, and the service over it."""

    cwd: Path
    settings: Settings
    service: GerritService
    ci_http: CiHttp | None = None

    def ci(self) -> CiContentService:
        """The CI content service for this repository and host."""
        return CiContentService.from_settings(
            self.cwd, self.settings, web_base=self.service.web_base, http=self.ci_http, refresh=self.service.refresh
        )
