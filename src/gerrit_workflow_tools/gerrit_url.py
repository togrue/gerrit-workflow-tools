from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from gerrit_workflow_tools.config import gerrit_remote, gerrit_url, gerrit_web_url
from gerrit_workflow_tools.git_run import git_out


def remote_push_url(cwd: Path | str | None, remote: str | None = None) -> str:
    r = remote or gerrit_remote(cwd)
    return git_out("remote", "get-url", r, cwd=cwd)


def push_url_to_gerrit_web_base(push_url: str) -> str:
    """
    Derive Gerrit web/API host base (no trailing slash, no /a/) from a git remote URL.
    """
    u = push_url.strip()
    if u.startswith("git@"):
        m = re.match(r"git@([^:]+):(.+)", u)
        if m:
            return f"https://{m.group(1)}"
    parsed = urlparse(u)
    if parsed.scheme in ("ssh", "git+ssh"):
        host = parsed.hostname or ""
        if not host and parsed.netloc:
            host = parsed.netloc.split("@")[-1].split(":")[0]
        # Typical Gerrit git port 29418; HTTPS is usually on 443.
        # if parsed.port and parsed.port not in (22, 29418):
        #     return f"https://{host}:{parsed.port}"
        # return f"https://{host}:"
        return f"http://{host}:8080"
    if parsed.scheme in ("http", "https"):
        return urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))
    raise ValueError(f"unsupported remote URL for Gerrit base: {push_url!r}")


def resolve_gerrit_web_base(cwd: Path | str | None) -> str:
    direct = gerrit_url(cwd)
    if direct:
        return direct.rstrip("/")
    override = gerrit_web_url(cwd)
    if override:
        return override.rstrip("/")
    return push_url_to_gerrit_web_base(remote_push_url(cwd))
