"""What a Gerrit cache row holds: the keys and ChangeInfo fields it is stored under, payloads read back.

Pure functions over payloads, no SQLite: :mod:`gerrit_workflow_tools.core.gerrit.cache` stores and
serves the rows.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def json_dumps(value: Any) -> str:
    """*value* as the compact, key-sorted JSON a row stores."""
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def strip_avatars(value: Any) -> Any:
    """Drop avatar URL arrays from ChangeInfo payloads before persistence."""

    if isinstance(value, dict):
        return {key: strip_avatars(item) for key, item in value.items() if key != "avatars"}
    if isinstance(value, list):
        return [strip_avatars(item) for item in value]
    return value


def footer_of_triplet(triplet: str) -> str:
    """The last ``~`` segment of a cache key: the Change-Id of a triplet, the number of ``project~N``."""
    return triplet.rsplit("~", 1)[-1]


def payload_triplet(payload: dict[str, Any]) -> str | None:
    """Gerrit's ``id`` of a ChangeInfo, or ``None``."""
    raw = payload.get("id")
    return raw if isinstance(raw, str) and raw else None


def payload_updated(payload: dict[str, Any]) -> str | None:
    """``updated`` of a ChangeInfo, or ``None``."""
    raw = payload.get("updated")
    return raw if isinstance(raw, str) else None


def payload_number(payload: dict[str, Any]) -> int | None:
    """``_number`` of a ChangeInfo, or ``None``."""
    raw = payload.get("_number")
    return raw if isinstance(raw, int) else None


def dict_rows(raw: Any) -> list[dict[str, Any]]:
    """The object rows of a JSON list; anything else reads as no rows."""
    return [row for row in raw if isinstance(row, dict)] if isinstance(raw, list) else []


def decode_comments(payload: Any) -> dict[str, list[dict[str, Any]]] | None:
    """A stored comments row as ``{path: [comment, ...]}``, or ``None`` when it is not one."""
    if not isinstance(payload, dict):
        return None
    return {str(path): dict_rows(rows) for path, rows in payload.items() if isinstance(rows, list)}


def decode_checks(payload: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
    """A stored checks row as ``(checks, messages)``, or ``None`` when it is not one."""
    if not isinstance(payload, dict):
        return None
    return dict_rows(payload.get("checks")), dict_rows(payload.get("messages"))


def comments_context_key(context_padding: int | None) -> str:
    """Cache discriminator for comment payloads with or without source context."""

    if context_padding is None:
        return ""
    return f"p{context_padding}"


@dataclass(frozen=True)
class CacheInfo:
    """Small summary for cache administration commands."""

    path: Path
    host: str
    changes: int
    comments: int
    checks: int
