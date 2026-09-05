"""Filesystem paths for shared Gerrit cache state."""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlparse


def gerrit_cache_host(web_base: str) -> str:
    """Return a filesystem-safe host key for a Gerrit web base URL."""

    parsed = urlparse(web_base.rstrip("/"))
    host = parsed.netloc or parsed.path
    host = host.split("@", 1)[-1].split(":", 1)[0]
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", host.strip().lower())
    return safe or "unknown"


def xdg_cache_home() -> Path:
    """Return XDG cache home with the standard ``~/.cache`` fallback."""

    raw = os.environ.get("XDG_CACHE_HOME")
    if raw and raw.strip():
        return Path(raw).expanduser()
    return Path.home() / ".cache"


def xdg_config_home() -> Path:
    """Return XDG config home with the standard ``~/.config`` fallback."""

    raw = os.environ.get("XDG_CONFIG_HOME")
    if raw and raw.strip():
        return Path(raw).expanduser()
    return Path.home() / ".config"


def gerrit_config_dir(web_base: str) -> Path:
    """Return the per-Gerrit-host directory for user-wide extension registries."""

    return xdg_config_home() / "ger" / gerrit_cache_host(web_base)


def gerrit_cache_dir(web_base: str) -> Path:
    """Return the per-Gerrit-host cache directory."""

    return xdg_cache_home() / "ger" / gerrit_cache_host(web_base)


def gerrit_cache_db_path(web_base: str) -> Path:
    """Return the SQLite cache DB path for *web_base*."""

    return gerrit_cache_dir(web_base) / "cache.db"


def gerrit_project_cache_key(project: str) -> str:
    """Return a filesystem-safe key for a Gerrit project path."""

    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", project.strip())
    return safe or "unknown"


def push_options_history_path(web_base: str, project: str) -> Path:
    """Return the per-(host, project) push-options history file path."""

    return gerrit_cache_dir(web_base) / "push_options_history" / f"{gerrit_project_cache_key(project)}.txt"
