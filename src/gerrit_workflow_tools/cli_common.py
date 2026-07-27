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
import logging
import sys
from collections.abc import Callable
from enum import IntEnum
from pathlib import Path

from gerrit_workflow_tools.cli_style import init_color_mode
from gerrit_workflow_tools.core.config import ConfigError, Settings
from gerrit_workflow_tools.core.gerrit.change_resolution import ChangeAmbiguousError, ChangeResolutionError
from gerrit_workflow_tools.core.gerrit.rest import GerritApiError, set_log_gerrit_response_bodies
from gerrit_workflow_tools.core.git_run import GitError
from gerrit_workflow_tools.summary_highlight import SummaryHighlighter, build_summary_highlighter

HELP_JSON = "Write machine-readable JSON to stdout."
HELP_COLOR = "Colorize output: always, auto, or never."
HELP_VERBOSE_PLACEHOLDER = "Reserved for richer command output in a future release (currently no effect)."
HELP_DEBUG_LOG = (
    "Log diagnostics to stderr (git commands, outcomes, resolved refs/URLs, decisions, and Gerrit API response bodies)."
)


class ExitCode(IntEnum):
    """One exit code per distinct failure reason, shared by every ``ger`` command.

    Codes are semantic, not per-command: the same reason exits the same way whichever
    command hit it. ``0``–``2`` are fixed by convention (argparse exits ``2`` on bad
    arguments); the rest split what used to be lumped together or reused.

    Contract: [docu/spec/exit-codes.md](../../docu/spec/exit-codes.md).
    """

    OK = 0
    ATTENTION = 1
    """Ran fine, but something wants the user: unresolved comments, failed CI, or the
    user declining a prompt. Not a failure."""
    USAGE = 2
    NOT_FOUND = 3
    """A changeish or Change-Id resolved to nothing."""
    AMBIGUOUS = 4
    """Several candidates survived narrowing."""
    GERRIT = 5
    """Gerrit answered badly, or not at all: HTTP, auth, unreachable."""
    CONFIG = 6
    """Required git configuration is missing (``gerrit.webUrl``, credentials)."""
    GIT = 7
    """A git command failed."""
    DUPLICATE_CHANGE_ID = 8
    """The same Change-Id appears on more than one local commit."""
    MISSING_CHANGE_ID = 9
    """A local commit has no Change-Id footer."""


# Ordered longest-subclass-first: ChangeAmbiguousError derives from ChangeResolutionError,
# and ConfigError from ValueError, so the more specific entry has to be matched first.
_FAILURE_EXITS: tuple[tuple[type[BaseException], ExitCode, str], ...] = (
    (ChangeAmbiguousError, ExitCode.AMBIGUOUS, "error"),
    (ChangeResolutionError, ExitCode.NOT_FOUND, "error"),
    (GerritApiError, ExitCode.GERRIT, "gerrit error"),
    (ConfigError, ExitCode.CONFIG, "error"),
    (GitError, ExitCode.GIT, "error"),
)


def run_cli_command(body: Callable[[], int]) -> int:
    """Run a command body, turning known failures into a message and an exit code.

    The single place the error-to-exit-code contract lives. Commands raise; only this
    decides what a failure is worth.

    Deliberately catches nothing else: an unmapped exception is a bug and should surface
    as a traceback rather than a tidy exit code. ``SystemExit`` (argparse ``--help`` and
    usage errors) and ``KeyboardInterrupt`` derive from ``BaseException`` and pass through
    untouched, as do exit codes a command returns after running a child process — those
    are git's codes, not ours.
    """
    try:
        return body()
    except Exception as error:  # pylint: disable=broad-exception-caught
        for failure_type, code, prefix in _FAILURE_EXITS:
            if isinstance(error, failure_type):
                print(f"{prefix}: {error}", file=sys.stderr)
                return int(code)
        raise


def add_follow_merges_args(parser: argparse.ArgumentParser) -> None:
    """Register ``--follow-merges`` (used by commands that display a local stack)."""
    parser.add_argument(
        "--follow-merges",
        action="store_true",
        default=False,
        help=(
            "Traverse all commit parents (including merge commits) instead of "
            "only the first-parent chain. By default only the first-parent chain "
            "is shown, matching Gerrit's relation-chain semantics."
        ),
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
) -> None:
    """Register ``-v``/``--verbose`` and ``--debug-log``.

    Pass *verbose_help* when a command uses ``--verbose`` for richer output instead of
    the package-wide placeholder text.
    """
    v_help = verbose_help or HELP_VERBOSE_PLACEHOLDER
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


def configure_logging(verbosity: int | bool) -> None:
    """Set package log level based on debug logging enablement.

    False → WARNING (silent)
    True  → DEBUG   (git subprocesses, outcomes, resolved refs/URLs, HTTP URLs/summaries,
                          and full API JSON bodies)
    """
    global _CONFIGURED, _DEBUG_LOG_ENABLED  # pylint: disable=global-statement
    v = int(verbosity)
    _DEBUG_LOG_ENABLED = bool(v)
    set_log_gerrit_response_bodies(_DEBUG_LOG_ENABLED)
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


def init_cli_runtime(*, debug_log: int | bool, color: str) -> tuple[Path, Settings, SummaryHighlighter]:
    """Configure logging/color and return ``(cwd, settings, summary_highlighter)`` for CLI commands.

    This is where a command's :class:`Settings` snapshot is taken: once, at the entry
    point, from a single ``git config --list``. Everything below receives it as an
    argument rather than reading configuration again.
    """

    configure_logging(debug_log)
    cwd = cwd_from_env()
    init_color_mode(color=color)
    settings = Settings.from_cwd(cwd)
    return cwd, settings, build_summary_highlighter(settings)


def handle_git_error(e: Exception) -> int:
    """Print a :class:`~gerrit_workflow_tools.git_run.GitError` and return :attr:`ExitCode.GIT`.

    Retained for commands not yet routed through :func:`run_cli_command`; it now uses the
    same exit code and message prefix, so the two agree.
    """
    if isinstance(e, GitError):
        print(f"error: {e.args[0]}", file=sys.stderr)
        return int(ExitCode.GIT)
    raise e
