from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

from gerrit_workflow_tools.git_run import GitError

_LOG = logging.getLogger("gerrit_workflow_tools")
_CONFIGURED = False


def configure_logging(verbosity: int | bool) -> None:
    """Set package log level based on verbosity count.

    0 / False  → WARNING (silent)
    1 / True   → INFO    (counts and resolution steps)
    2+         → DEBUG   (full API response bodies)
    """
    global _CONFIGURED
    v = int(verbosity)
    if v >= 2:
        level = logging.DEBUG
    elif v == 1:
        level = logging.INFO
    else:
        level = logging.WARNING
    _LOG.setLevel(level)
    if not _CONFIGURED:
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(logging.Formatter("%(levelname)s: %(name)s: %(message)s"))
        _LOG.addHandler(h)
        _LOG.propagate = False
        _CONFIGURED = True


def cwd_from_env() -> Path:
    """Return the current working directory (repository root for CLI commands)."""
    return Path.cwd()


def print_json(obj: Any) -> None:
    """Print *obj* as indented JSON to stdout."""
    print(json.dumps(obj, indent=2))


def handle_git_error(e: Exception) -> int:
    """Print a :class:`~gerrit_workflow_tools.git_run.GitError` and return 1; re-raise other exceptions."""
    if isinstance(e, GitError):
        print(e.args[0], file=sys.stderr)
        return 1
    raise e
