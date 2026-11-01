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
import shlex
import sys
from collections.abc import Callable, Mapping
from enum import IntEnum
from pathlib import Path
from typing import TypeVar

from gerrit_workflow_tools.cli_style import (
    ANSI_DIM,
    color_text,
    ensure_utf8_stdio,
    init_color_mode,
    init_hyperlink_mode,
)
from gerrit_workflow_tools.core.call_trace import print_call_trace_summary, set_call_trace_enabled
from gerrit_workflow_tools.core.config import ConfigError, Settings
from gerrit_workflow_tools.core.gerrit.change_resolution import (
    ChangeAmbiguousError,
    ChangeResolutionError,
    Resolution,
    format_resolution_note,
)
from gerrit_workflow_tools.core.gerrit.rest import GerritApiError, set_log_gerrit_response_bodies
from gerrit_workflow_tools.core.git_run import GitError, git
from gerrit_workflow_tools.core.git_state import rebase_in_progress
from gerrit_workflow_tools.summary_highlight import SummaryHighlighter, build_summary_highlighter


_T = TypeVar("_T")

HELP_JSON = "Write machine-readable JSON to stdout."
HELP_COLOR = "Colorize output: always, auto, or never."
HELP_HYPERLINKS = "Emit OSC 8 terminal hyperlinks: always, auto, or never."
HELP_VERBOSE_PLACEHOLDER = "Reserved for richer command output in a future release (currently no effect)."
HELP_DEBUG_LOG = (
    "Log diagnostics to stderr (git commands, outcomes, resolved refs/URLs, decisions, Gerrit API "
    "response bodies, and a high-level request timing summary)."
)


class ExitCode(IntEnum):
    """One exit code per distinct failure reason, shared by every ``ger`` command.

    Codes are semantic, not per-command: the same reason exits the same way whichever
    command hit it. ``0``-``2`` are fixed by convention (argparse exits ``2`` on bad
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
    finally:
        print_call_trace_summary()


def print_note(note: str | None) -> None:
    """Print a transparency note, dimmed, on stderr so stdout stays machine-readable."""
    if note:
        print(color_text(note, ANSI_DIM), file=sys.stderr)


def print_resolution_note(resolution: Resolution | None) -> None:
    """Print how *resolution* narrowed, when Gerrit was consulted and it did."""
    if resolution is not None:
        print_note(format_resolution_note(resolution))


def ask_choice(prompt: str, answers: Mapping[str, _T], *, hint: str | None = None) -> _T:
    """Ask until the lower-cased reply is a key of *answers*; return what it maps to."""
    while True:
        reply = input(prompt).strip().lower()
        if reply in answers:
            return answers[reply]
        if hint:
            print(hint, file=sys.stderr)


def confirm(question: str, *, default: bool) -> bool:
    """Ask a yes/no *question*. Enter takes *default*; anything else unknown asks again."""
    suffix = "[Y/n]" if default else "[y/N]"
    return ask_choice(f"{question} {suffix}: ", {"y": True, "yes": True, "n": False, "no": False, "": default})


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


def add_json_arg(parser: argparse.ArgumentParser | argparse._MutuallyExclusiveGroup) -> None:
    """Register ``--json``, read back as ``args.json_``."""
    parser.add_argument("--json", action="store_true", dest="json_", help=HELP_JSON)


def add_color_args(parser: argparse.ArgumentParser) -> None:
    """Register shared color-output and hyperlink flags."""
    parser.add_argument(
        "--color",
        choices=("always", "auto", "never"),
        default="auto",
        metavar="WHEN",
        help=HELP_COLOR,
    )
    parser.add_argument(
        "--hyperlinks",
        choices=("always", "auto", "never"),
        default="auto",
        metavar="WHEN",
        help=HELP_HYPERLINKS,
    )


def add_verbose_and_debug_log_args(
    parser: argparse.ArgumentParser,
    *,
    debug_log_help: str | None = None,
    verbose_help: str | None = None,
    verbose_count: bool = False,
) -> None:
    """Register ``-v``/``--verbose`` and ``--debug-log``.

    Pass *verbose_help* when a command uses ``--verbose`` for richer output instead of
    the package-wide placeholder text. With *verbose_count*, ``-v`` may be repeated
    (e.g. ``-vv``); ``args.verbose`` is an ``int`` (0 when omitted).
    """
    v_help = verbose_help or HELP_VERBOSE_PLACEHOLDER
    parser.add_argument(
        "-v",
        "--verbose",
        action="count" if verbose_count else "store_true",
        default=0 if verbose_count else False,
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


def init_cli_runtime(args: argparse.Namespace) -> tuple[Path, Settings, SummaryHighlighter]:
    """Configure logging/color from parsed *args*; return ``(cwd, settings, summary_highlighter)``.

    Commands without ``--debug-log`` or ``--color`` get logging off and color on ``auto``.

    This is where a command's :class:`Settings` snapshot is taken: once, at the entry
    point, from a single ``git config --list``. Everything below receives it as an
    argument rather than reading configuration again.
    """

    ensure_utf8_stdio()
    configure_logging(getattr(args, "debug_log", False))
    cwd = Path.cwd()
    init_color_mode(color=getattr(args, "color", "auto"))
    init_hyperlink_mode(hyperlinks=getattr(args, "hyperlinks", "auto"))
    settings = Settings.from_cwd(cwd)
    return cwd, settings, build_summary_highlighter(settings)


def require_no_rebase_in_progress(cwd: Path) -> None:
    """Raise :class:`GitError` when a rebase is underway, so a new one could not start."""
    if rebase_in_progress(cwd):
        raise GitError("a rebase is already in progress; run `git rebase --continue` or `git rebase --abort` first")


def run_interactive_rebase(cwd: Path, base: str, *, sequence_editor: str, env: dict[str, str]) -> int:
    """``git rebase -i`` with package module *sequence_editor* as editor; return git's exit code."""
    editor = f"{shlex.quote(sys.executable)} -m gerrit_workflow_tools.{sequence_editor}"
    env = {**env, "GIT_SEQUENCE_EDITOR": editor}
    return git("rebase", "-i", base, cwd=cwd, env=env, check=False, capture=False).returncode
