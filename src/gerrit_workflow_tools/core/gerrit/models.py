"""Small object wrappers for Gerrit REST payloads."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Change:
    """Object view over Gerrit ``ChangeInfo``."""

    payload: dict[str, Any]

    @property
    def change_id(self) -> str | None:
        raw = self.payload.get("change_id")
        return raw if isinstance(raw, str) else None

    @property
    def number(self) -> int | None:
        raw = self.payload.get("_number")
        return raw if isinstance(raw, int) else None

    @property
    def updated(self) -> str | None:
        raw = self.payload.get("updated")
        return raw if isinstance(raw, str) else None


@dataclass(frozen=True)
class Account:
    """Object view over Gerrit ``AccountInfo``."""

    payload: dict[str, Any]

    @property
    def account_id(self) -> int | None:
        raw = self.payload.get("_account_id")
        return raw if isinstance(raw, int) else None

    @property
    def username(self) -> str | None:
        raw = self.payload.get("username")
        return raw if isinstance(raw, str) else None


@dataclass(frozen=True)
class Comment:
    """Object view over Gerrit ``CommentInfo`` with its file path."""

    path: str
    payload: dict[str, Any]

    @property
    def comment_id(self) -> str | None:
        raw = self.payload.get("id")
        return raw if isinstance(raw, str) else None

    @property
    def message(self) -> str:
        raw = self.payload.get("message")
        return raw if isinstance(raw, str) else ""


@dataclass(frozen=True)
class ReviewerSlot:
    """Reviewer assignment target for REST mutations."""

    reviewer: str
    state: str = "REVIEWER"
