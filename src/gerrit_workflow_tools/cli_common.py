"""The shared CLI runtime: exit codes, the run's bootstrap, and where failures map to codes.

The argparse groups commands share live in :mod:`gerrit_workflow_tools.cli_args`.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from contextvars import ContextVar
from enum import IntEnum
from pathlib import Path

# The `as` names are re-exported for cli_review.py only, until it imports them from cli_args
# (test_argument_groups_come_from_cli_args tracks it). Import them from cli_args.
from gerrit_workflow_tools.cli_args import (
    add_change_option as add_change_option,
    add_color_args as add_color_args,
    add_output_format_args as add_output_format_args,
    add_verbose_and_debug_log_args as add_verbose_and_debug_log_args,
    changeish_help as changeish_help,
    output_format,
)
from gerrit_workflow_tools.cli_logging import configure_logging
from gerrit_workflow_tools.cli_terminal import assume_yes, confirm, stdin_is_terminal
from gerrit_workflow_tools.core.attention import NoEditAttentionError
from gerrit_workflow_tools.core.call_trace import format_call_trace_summary
from gerrit_workflow_tools.core.ci_content import CiContentError
from gerrit_workflow_tools.core.comment_target import MissingChangeIdError
from gerrit_workflow_tools.core.config import ConfigError, Settings
from gerrit_workflow_tools.core.gerrit.change_resolution import ChangeAmbiguousError, ChangeResolutionError
from gerrit_workflow_tools.core.gerrit.rest import GerritApiError, GerritRest
from gerrit_workflow_tools.core.gerrit.stack_context import NoWorkingBranchError
from gerrit_workflow_tools.core.git_run import GitError, UnknownRevisionError
from gerrit_workflow_tools.core.review_checkout import ReviewChainEndError
from gerrit_workflow_tools.core.run_context import RunContext
from gerrit_workflow_tools.core.upstream import NeedsUpstreamError
from gerrit_workflow_tools.core.usage import Refusal, UsageError
from gerrit_workflow_tools.registry_trust_prompt import settle_registry_trust
from gerrit_workflow_tools.render.style import (
    ensure_utf8_stdio,
    init_color_mode,
    init_hyperlink_mode,
    init_terminal_width,
)
from gerrit_workflow_tools.upstream_interactive import ask_for_needed_upstream, with_suggested_upstream


class ExitCode(IntEnum):
    """One exit code per distinct failure reason, shared by every ``ger`` command.

    Codes are semantic, not per-command: the same reason exits the same way whichever
    command hit it. ``0`` means the command did what was asked, whatever state it reports;
    every other code is a reason it did not. ``0``-``2`` are fixed by convention (argparse exits ``2`` on bad
    arguments); the rest split what used to be lumped together or reused.

    Contract: [docu/spec/exit-codes.md](../../docu/spec/exit-codes.md).
    """

    OK = 0
    REFUSED = 1
    """Did not go ahead: the user declined a prompt, or ger will not proceed without the
    user's decision. A command that ran and merely reports attention exits ``OK``."""
    USAGE = 2
    NOT_FOUND = 3
    """A changeish, Change-Id or git revision resolved to nothing."""
    AMBIGUOUS = 4
    """Several candidates survived narrowing."""
    GERRIT = 5
    """Gerrit answered badly, or not at all: HTTP, auth, unreachable."""
    CONFIG = 6
    """Required git configuration is missing (``ger.webUrl``, credentials)."""
    GIT = 7
    """A git command failed."""
    DUPLICATE_CHANGE_ID = 8
    """The same Change-Id appears on more than one local commit."""
    MISSING_CHANGE_ID = 9
    """A local commit has no Change-Id footer."""
    NO_EDIT_ATTENTION = 10
    """An attention walk has nothing left to visit. Scripts loop until they see this."""
    CI_CONTENT = 11
    """A **CI link**'s content could not be fetched or parsed where a command required it (``ger ci``)."""


# Ordered longest-subclass-first: ChangeAmbiguousError, MissingChangeIdError and NoWorkingBranchError derive from
# ChangeResolutionError,
# and ConfigError from ValueError, so the more specific entry has to be matched first.
_FAILURE_EXITS: tuple[tuple[type[BaseException], ExitCode, str], ...] = (
    (ChangeAmbiguousError, ExitCode.AMBIGUOUS, "error"),
    (MissingChangeIdError, ExitCode.MISSING_CHANGE_ID, "error"),
    (NoWorkingBranchError, ExitCode.USAGE, "error"),
    (ChangeResolutionError, ExitCode.NOT_FOUND, "error"),
    (GerritApiError, ExitCode.GERRIT, "gerrit error"),
    (ConfigError, ExitCode.CONFIG, "error"),
    (UsageError, ExitCode.USAGE, "error"),
    (Refusal, ExitCode.REFUSED, "error"),
    (UnknownRevisionError, ExitCode.NOT_FOUND, "error"),
    # A walk with nowhere left to go: reported without an "error:" prefix, since scripts loop on it.
    (NoEditAttentionError, ExitCode.NO_EDIT_ATTENTION, ""),
    (ReviewChainEndError, ExitCode.NO_EDIT_ATTENTION, "error"),
    (CiContentError, ExitCode.CI_CONTENT, "ci error"),
    (GitError, ExitCode.GIT, "error"),
)


def failure_exit(error: BaseException) -> tuple[ExitCode, str] | None:
    """The exit code and message prefix a known failure is worth; ``None`` for anything else (a bug)."""
    for failure_type, code, prefix in _FAILURE_EXITS:
        if isinstance(error, failure_type):
            return code, prefix
    return None


_MAY_ASK_FOR_UPSTREAM: ContextVar[bool] = ContextVar("may_ask_for_upstream", default=False)
"""Whether the running command may ask for a missing upstream: set by :func:`init_cli_runtime` from its args."""


def run_cli_command(body: Callable[[], int]) -> int:
    """Run a command body, turning known failures into a message and an exit code.

    The single place the error-to-exit-code contract lives. Commands raise; only this
    decides what a failure is worth.

    Deliberately catches nothing else: an unmapped exception is a bug and should surface
    as a traceback rather than a tidy exit code. ``SystemExit`` (argparse ``--help`` and
    usage errors) and ``KeyboardInterrupt`` derive from ``BaseException`` and pass through
    untouched, as do exit codes a command returns after running a child process — those
    are git's codes, not ours.

    It is also the one place a missing upstream is asked for, however deep the need: on a
    :class:`NeedsUpstreamError` (raised reading the stack, before anything changed) the user
    sets the upstream and *body* runs again from the start; declining exits ``REFUSED``.
    """
    scope = _MAY_ASK_FOR_UPSTREAM.set(False)
    asked: set[str] = set()
    try:
        while True:
            try:
                return body()
            except NeedsUpstreamError as error:
                answer = ask_for_needed_upstream(error, asked) if _MAY_ASK_FOR_UPSTREAM.get() else None
                if answer is None:
                    return _report_failure(with_suggested_upstream(error))
                if not answer:
                    return int(ExitCode.REFUSED)
            except Exception as error:  # pylint: disable=broad-exception-caught
                return _report_failure(error)
    finally:
        _MAY_ASK_FOR_UPSTREAM.reset(scope)
        print_call_trace_summary()


def print_call_trace_summary() -> None:
    """Print the ``--debug-log`` timing report to stderr when tracing collected any calls."""
    summary = format_call_trace_summary()
    if summary:
        sys.stdout.flush()
        print(f"\n{summary}", file=sys.stderr)


def _report_failure(error: Exception) -> int:
    """Print a known failure and return its exit code; re-raise anything else (a bug)."""
    failure = failure_exit(error)
    if failure is None:
        raise error
    code, prefix = failure
    sys.stdout.flush()  # What the command did show comes first, the reason it stopped last.
    print(f"{prefix}: {error}" if prefix else str(error), file=sys.stderr)
    return int(code)


def confirm_publish(args: argparse.Namespace, what: str) -> bool:
    """Whether to publish *what*: ``-y`` (or ``GER_ASSUME_YES``), else a yes at ``Publish <what>?``.

    Without a prompt to answer (no terminal, ``--json``, ``--ai``) it raises :class:`UsageError`: pass ``-y``.
    """
    if args.yes or assume_yes():
        return True
    if not may_prompt(args):
        raise UsageError("no prompt to answer (non-interactive, --json or --ai): pass -y to publish")
    return confirm(f"Publish {what}?", default=False)


def may_prompt(args: argparse.Namespace) -> bool:
    """Whether a command may ask: human output on a terminal. ``--json`` and ``--ai`` output never prompts."""
    return output_format(args) == "human" and stdin_is_terminal()


# Set by `ger --refresh` (cli_ger) for this process and the git-run editors it starts,
# such as the rebase enricher. Read here, once per run, and nowhere below.
REFRESH_ENV = "GER_CACHE_REFRESH"


def refresh_requested() -> bool:
    """Whether this process runs under ``ger --refresh``."""
    return os.environ.get(REFRESH_ENV, "").strip().lower() in ("1", "true", "yes")


def init_cli_runtime(
    args: argparse.Namespace, *, repository: bool = True, gerrit: GerritRest | None = None
) -> RunContext:
    """Configure logging/color from parsed *args*; return the **run context**.

    Commands without ``--debug-log`` or ``--color`` get logging off and color on ``auto``.
    Outside a git repository a command that needs one (*repository*, the default) fails here,
    the same way for every command, before it can misreport what it would have looked up.

    This is where a command's :class:`Settings` snapshot is taken: once, at the entry
    point, from a single ``git config --list --show-scope``. Everything below receives it as an
    argument rather than reading configuration again. *gerrit* replaces the configured host
    (tests); ``ger --refresh`` reaches the run's service from here. Before the command runs, the
    user is asked whether this project's untrusted ``registry.py`` files may run.
    """

    ensure_utf8_stdio()
    configure_logging(getattr(args, "debug_log", False))
    cwd = Path.cwd()
    plain = getattr(args, "format_", None) == "ai"  # Text for a model, Markdown or not: no colour, no links.
    init_color_mode(color="never" if plain else getattr(args, "color", "auto"))
    init_hyperlink_mode(hyperlinks="never" if plain else getattr(args, "hyperlinks", "auto"))
    init_terminal_width(plain=plain)
    settings = Settings.from_cwd(cwd)
    _MAY_ASK_FOR_UPSTREAM.set(may_prompt(args) and not getattr(args, "dry_run", False))
    if repository and not settings.in_repository:
        raise GitError("not a git repository; run ger inside a clone")
    refresh = refresh_requested() or bool(getattr(args, "refresh", False))
    run = RunContext.open(cwd, settings=settings, gerrit=gerrit, refresh=refresh)
    settle_registry_trust(run, confirm=confirm if may_prompt(args) else None)
    return run
