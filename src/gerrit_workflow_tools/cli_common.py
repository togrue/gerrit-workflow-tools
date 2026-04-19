"""Shared CLI helpers and argparse conventions for ``gerrit_workflow_tools`` CLIs.

Help text style (``help=`` on parsers and arguments):

- Imperative mood, sentence case, and end each string with a period (consistent).
- When a flag only affects package logging, phrase it as "Log … to stderr".
- Shared flag text lives in ``HELP_*`` constants in this module; CLIs should use them.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from gerrit_workflow_tools.git_run import GitError

HELP_JSON = "Write machine-readable JSON to stdout."
HELP_IGNORE_PATTERN = "Ignore this configured stop pattern (repeatable)."
HELP_NO_CONFIG_PATTERNS = "Do not use gerrit.stopPattern values from config."
HELP_NO_COLOR = "Disable colored output."


def add_stop_pattern_args(parser: argparse.ArgumentParser) -> None:
    """Register ``--ignore-pattern`` and ``--no-config-patterns`` (used by ``ger push``)."""
    parser.add_argument(
        "--ignore-pattern",
        action="append",
        default=[],
        metavar="REGEX",
        help=HELP_IGNORE_PATTERN,
    )
    parser.add_argument(
        "--no-config-patterns",
        action="store_true",
        help=HELP_NO_CONFIG_PATTERNS,
    )


def add_color_args(parser: argparse.ArgumentParser) -> None:
    """Register shared color-output flags."""
    parser.add_argument("--no-color", action="store_true", help=HELP_NO_COLOR)


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
