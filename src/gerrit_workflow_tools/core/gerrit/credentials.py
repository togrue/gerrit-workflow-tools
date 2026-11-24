"""Gerrit credentials: which HTTP Basic user and secret go to a Gerrit host."""

from __future__ import annotations

import base64
from dataclasses import dataclass

from gerrit_workflow_tools.core.config import Settings


@dataclass(frozen=True)
class GerritAuth:
    """Resolved HTTP Basic credentials for one Gerrit host."""

    user: str
    secret: str

    def header(self) -> str:
        """Return the ``Authorization`` header value."""
        token = base64.b64encode(f"{self.user}:{self.secret}".encode()).decode()
        return f"Basic {token}"


def gerrit_auth_from_settings(settings: Settings) -> GerritAuth | None:
    """Read ``ger.user`` plus ``ger.token``/``ger.password``, or ``None`` when unset."""
    user = settings.gerrit_user
    secret = settings.gerrit_token or settings.gerrit_password
    if not user or secret is None:
        return None
    return GerritAuth(user=user, secret=secret)
