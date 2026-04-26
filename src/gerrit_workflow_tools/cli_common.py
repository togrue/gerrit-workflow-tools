"""Shared CLI helpers and argparse conventions for ``gerrit_workflow_tools`` CLIs.

Help text style (``help=`` on parsers and arguments):

- Imperative mood, sentence case, and end each string with a period (consistent).
- When a flag only affects package logging, phrase it as "Log … to stderr".
- Shared flag text lives in ``HELP_*`` constants in this module; CLIs should use them.
- Use :func:`add_verbose_and_debug_log_args` for ``-v``/``--verbose`` (placeholder) and
  ``--debug-log`` (diagnostic logging to stderr); do not use ``--verbose`` for logging.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from gerrit_workflow_tools.git_run import GitError
from gerrit_workflow_tools.summary_highlight import SummaryHighlighter, build_summary_highlighter

HELP_JSON = "Write machine-readable JSON to stdout."
HELP_IGNORE_PATTERN = "Ignore this configured stop pattern (repeatable)."
HELP_COLOR = "Colorize output: always, auto, or never."
HELP_VERBOSE_PLACEHOLDER = "Reserved for richer command output in a future release (currently no effect)."
HELP_DEBUG_LOG = (
    "Log diagnostics to stderr (git commands, outcomes, resolved refs/URLs, decisions, and Gerrit API response bodies)."
)


def add_stop_pattern_args(parser: argparse.ArgumentParser) -> None:
    """Register ``--ignore-pattern`` (used by ``ger push``)."""
    parser.add_argument(
        "--ignore-pattern",
        action="append",
        default=[],
        metavar="REGEX",
        help=HELP_IGNORE_PATTERN,
    )


def add_color_args(parser: argparse.ArgumentParser) -> None:
    """Register shared color-output flags."""
    parser.add_argument(
        "--color",
        choices=("always", "auto", "never"),
        default="auto",
        metavar="WHEN",
        help=HELP_COLOR,
    )


def add_verbose_and_debug_log_args(
    parser: argparse.ArgumentParser,
    *,
    debug_log_help: str | None = None,
    verbose_help: str | None = None,
    verbose_action: str = "store_true",
) -> None:
    """Register ``-v``/``--verbose`` and ``--debug-log``.

    Pass *verbose_help* when a command uses ``--verbose`` for richer output instead of
    the package-wide placeholder text.

    *verbose_action*: ``\"store_true\"`` (default) or ``\"count\"`` (``-v``/``-vv`` adds
    levels; default ``0`` when omitted).
    """
    v_help = verbose_help or HELP_VERBOSE_PLACEHOLDER
    if verbose_action == "count":
        parser.add_argument(
            "-v",
            "--verbose",
            action="count",
            default=0,
            help=v_help,
        )
    else:
        parser.add_argument(
            "-v",
            "--verbose",
            action="store_true",
            help=v_help,
        )
    parser.add_argument(
        "--debug-log",
        action="store_true",
        help=debug_log_help or HELP_DEBUG_LOG,
    )


_LOG = logging.getLogger("gerrit_workflow_tools")
_CONFIGURED = False
_DEBUG_LOG_ENABLED = False


def log_gerrit_response_bodies() -> bool:
    """Whether to log full Gerrit JSON bodies when debug logging is enabled."""
    return _DEBUG_LOG_ENABLED


def configure_logging(verbosity: int | bool) -> None:
    """Set package log level based on debug logging enablement.

    False → WARNING (silent)
    True  → DEBUG   (git subprocesses, outcomes, resolved refs/URLs, HTTP URLs/summaries,
                          and full API JSON bodies)
    """
    global _CONFIGURED, _DEBUG_LOG_ENABLED  # pylint: disable=global-statement
    v = int(verbosity)
    _DEBUG_LOG_ENABLED = bool(v)
    level = logging.DEBUG if v >= 1 else logging.WARNING
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


def init_cli_runtime(*, debug_log: int | bool, color: str) -> tuple[Path, SummaryHighlighter]:
    """Configure logging/color and return ``(cwd, summary_highlighter)`` for CLI commands."""
    from gerrit_workflow_tools.cli_style import init_color_mode

    configure_logging(debug_log)
    cwd = cwd_from_env()
    init_color_mode(color=color)
    return cwd, build_summary_highlighter(cwd)


def print_json(obj: Any) -> None:
    """Print *obj* as indented JSON to stdout."""
    print(json.dumps(obj, indent=2))


def handle_git_error(e: Exception) -> int:
    """Print a :class:`~gerrit_workflow_tools.git_run.GitError` and return 1; re-raise other exceptions."""
    if isinstance(e, GitError):
        print(e.args[0], file=sys.stderr)
        return 1
    raise e
