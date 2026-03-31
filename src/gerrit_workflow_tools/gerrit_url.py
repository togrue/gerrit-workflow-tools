from __future__ import annotations

from pathlib import Path

from gerrit_workflow_tools.config import gerrit_web_url


def resolve_gerrit_web_base(cwd: Path | str | None) -> str:
    """
    Gerrit HTTPS base for the REST API and web links.

    Requires ``gerrit.webUrl`` in git config (no inference from remotes).
    """
    override = gerrit_web_url(cwd)
    if override:
        return override.rstrip("/")
    raise ValueError(
        "gerrit.webUrl is not set; configure the Gerrit HTTPS base, e.g. "
        "`git config gerrit.webUrl https://gerrit.example.com`"
    )
