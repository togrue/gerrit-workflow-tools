"""Helpers for resolving Gerrit changes when loading review-thread data."""

from __future__ import annotations

import logging
from typing import Any

from gerrit_workflow_tools.core.gerrit_change_status import LOG_QUERY_OPTIONS
from gerrit_workflow_tools.core.gerrit_client import (
    GerritClient,
    pick_change_from_query_result,
    resolve_change_ref,
)
from gerrit_workflow_tools.core.git_run import GitError

logger = logging.getLogger(__name__)


def resolve_gerrit_change(
    client: GerritClient,
    *,
    change_arg: str | None,
    local_change_id: str | None,
) -> dict[str, Any]:
    """Resolve a Gerrit change query *change_arg* or *local_change_id* to a single change dict."""
    opts = list(LOG_QUERY_OPTIONS)
    if change_arg:
        q = resolve_change_ref(change_arg)
        rows = client.query_changes(q, n=10, options=opts)
        ch = pick_change_from_query_result(rows)
    elif local_change_id:
        rows = client.query_changes(f"change:{local_change_id}", n=10, options=opts)
        ch = pick_change_from_query_result(rows)
    else:
        raise GitError("internal: no change specified")
    logger.info(
        "resolved change -> #%s %r (id=%s)",
        ch.get("_number"),
        ch.get("subject"),
        ch.get("id"),
    )
    logger.debug("resolved change detail: %s", ch)
    return ch
