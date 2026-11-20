"""Package diagnostic logging for ``--debug-log``: one stderr handler, the call trace and Gerrit response bodies."""

from __future__ import annotations

import logging
import sys

from gerrit_workflow_tools.core.call_trace import set_call_trace_enabled
from gerrit_workflow_tools.core.gerrit.http_rest import set_log_gerrit_response_bodies


_LOG = logging.getLogger("gerrit_workflow_tools")
_CONFIGURED = False
_DEBUG_LOG_ENABLED = False


def configure_logging(verbosity: int | bool) -> None:
    """Set package log level based on debug logging enablement.

    False → WARNING (silent)
    True  → DEBUG   (git subprocesses, outcomes, resolved refs/URLs, HTTP URLs/summaries,
                          and full API JSON bodies)
    """
    global _CONFIGURED, _DEBUG_LOG_ENABLED  # pylint: disable=global-statement
    v = int(verbosity)
    _DEBUG_LOG_ENABLED = bool(v)
    set_call_trace_enabled(_DEBUG_LOG_ENABLED)
    set_log_gerrit_response_bodies(_DEBUG_LOG_ENABLED)
    level = logging.DEBUG if v >= 1 else logging.WARNING
    _LOG.setLevel(level)
    if not _CONFIGURED:
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(logging.Formatter("%(levelname)s: %(name)s: %(message)s"))
        _LOG.addHandler(h)
        _LOG.propagate = False
        _CONFIGURED = True
