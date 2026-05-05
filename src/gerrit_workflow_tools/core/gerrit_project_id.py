"""Resolve Gerrit project name from config or remote URL."""

from __future__ import annotations

import re
from pathlib import Path

from gerrit_workflow_tools.core.config import gerrit_project, gerrit_remote
from gerrit_workflow_tools.core.git_run import GitError, git_out


def parse_project_name_from_remote_url(remote_url: str) -> str | None:
    """Return Gerrit project path from a git remote URL."""
    s = remote_url.strip()
    if not s:
        return None

    # scp-like syntax: user@host:path/to/repo(.git)
    m = re.match(r"^[^@]+@[^:]+:(.+)$", s)
    if m:
        path = m.group(1)
    else:
        # URL syntax, including ssh://, http(s)://
        m_url = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://[^/]+/(.+)$", s)
        if not m_url:
            return None
        path = m_url.group(1)

    path = path.strip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    if path.startswith("a/"):
        path = path[2:]
    return path or None


def resolve_gerrit_project_name(cwd: Path | str | None) -> str | None:
    """Resolve project name for this repo, preferring ``gerrit.project``."""
    override = gerrit_project(cwd)
    if override:
        return override
    remote = gerrit_remote(cwd)
    try:
        url = git_out("remote", "get-url", remote, cwd=cwd)
    except GitError:
        return None
    return parse_project_name_from_remote_url(url)
