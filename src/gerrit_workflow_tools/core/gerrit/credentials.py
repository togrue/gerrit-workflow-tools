"""Gerrit credentials: which HTTP Basic user and secret go to a Gerrit host (#111)."""

from __future__ import annotations

import base64
from dataclasses import dataclass

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.config_keys import ConfigScope, gerrit_host_key


@dataclass(frozen=True)
class GerritAuth:
    """Resolved HTTP Basic credentials for one Gerrit host."""

    user: str
    secret: str

    def header(self) -> str:
        """Return the ``Authorization`` header value."""
        token = base64.b64encode(f"{self.user}:{self.secret}".encode()).decode()
        return f"Basic {token}"


def gerrit_user_for(settings: Settings, web_url: str | None) -> str | None:
    """The username for *web_url*: ``ger.<url>.user``, else ``ger.user`` (no secret, so a plain default)."""
    return (settings.get(gerrit_host_key(web_url, "user")) if web_url else None) or settings.get("ger.user")


def gerrit_secret_for(settings: Settings, web_url: str) -> str | None:
    """``ger.<url>.token``, ``….password``, else the unscoped one, which warns: it goes to any host."""
    for name in ("token", "password"):
        scoped = settings.get(gerrit_host_key(web_url, name))
        if scoped is not None:
            return scoped
    for name in ("token", "password"):
        key = f"ger.{name}"
        unscoped = settings.get(key)
        if unscoped is not None:
            settings.warn_once(key, _unscoped_warning(key, gerrit_host_key(web_url, name), settings.scope(key)))
            return unscoped
    return None


def _unscoped_warning(old: str, new: str, scope: ConfigScope | None) -> str:
    """One line saying *old* goes to any host, and the ``git config`` commands that move it to *new* at *scope*."""
    why = f"git config key {old} is sent to whatever host ger.webUrl names"
    if scope is None or scope.flag is None:  # `git -c`: nothing on disk to move
        return f"{why}; use {new}"
    git_config = f"git config {scope.flag}"
    return f'{why}; move it with: {git_config} {new} "$({git_config} {old})" && {git_config} --unset {old}'


def gerrit_auth_from_settings(settings: Settings, web_base: str | None = None) -> GerritAuth | None:
    """The credentials for *web_base* (default ``ger.webUrl``), or ``None`` when there are none."""
    base = web_base if web_base is not None else settings.gerrit_web_url
    if not base:
        return None
    user = gerrit_user_for(settings, base)
    secret = gerrit_secret_for(settings, base)
    if not user or secret is None:
        return None
    return GerritAuth(user=user, secret=secret)
