from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

from gerrit_workflow_tools.git_run import GitError

_LOG = logging.getLogger("gerrit_workflow_tools")
_CONFIGURED = False


def configure_logging(verbose: bool) -> None:
    """Enable DEBUG on package loggers; stderr handler is attached once."""
    global _CONFIGURED
    _LOG.setLevel(logging.DEBUG if verbose else logging.WARNING)
    if not _CONFIGURED:
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(logging.Formatter("%(levelname)s: %(name)s: %(message)s"))
        _LOG.addHandler(h)
        _LOG.propagate = False
        _CONFIGURED = True


def cwd_from_env() -> Path:
    return Path.cwd()


def print_json(obj: Any) -> None:
    print(json.dumps(obj, indent=2))


def handle_git_error(e: Exception) -> int:
    if isinstance(e, GitError):
        print(e.args[0], file=sys.stderr)
        return 1
    raise e
