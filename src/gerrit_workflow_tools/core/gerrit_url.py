"""Resolve and validate Gerrit web/API base URLs."""

from __future__ import annotations

import logging
from pathlib import Path

from gerrit_workflow_tools.core.config import gerrit_web_url

logger = logging.getLogger(__name__)


def resolve_gerrit_web_base(cwd: Path | str | None) -> str:
    """
    Gerrit HTTPS base for the REST API and web links.

    Requires ``gerrit.webUrl`` in git config (no inference from remotes).
    """
    override = gerrit_web_url(cwd)
    if override:
        base = override.rstrip("/")
        logger.debug("resolve_gerrit_web_base: gerrit.webUrl -> %s", base)
        return base
    raise ValueError(
        "gerrit.webUrl is not set; configure the Gerrit HTTPS base, e.g. "
        "`git config gerrit.webUrl https://gerrit.example.com`"
    )
