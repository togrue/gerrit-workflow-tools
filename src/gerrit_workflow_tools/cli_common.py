"""Shared CLI helpers and argparse conventions for ``gerrit_workflow_tools`` CLIs.

Help text style (``help=`` on parsers and arguments):

- Imperative mood, sentence case, and end each string with a period (consistent).
- When a flag only affects package logging, phrase it as "Log … to stderr".
- Shared flag text lives in ``HELP_*`` constants in this module; CLIs should use them.
- Use :func:`add_verbose_and_debug_log_args` for ``-v``/``--verbose`` and ``--debug-log``
  (diagnostic logging to stderr); do not use ``--verbose`` for logging.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Mapping
from contextvars import ContextVar
from enum import IntEnum
from pathlib import Path
from typing import TypeVar

from gerrit_workflow_tools.cli_logging import configure_logging
from gerrit_workflow_tools.cli_style import (
    ANSI_DIM,
    color_text,
    ensure_utf8_stdio,
    init_color_mode,
    init_hyperlink_mode,
)
from gerrit_workflow_tools.core.attention import NoEditAttentionError
from gerrit_workflow_tools.core.call_trace import print_call_trace_summary
from gerrit_workflow_tools.core.changeish import as_change_only
from gerrit_workflow_tools.core.ci_content import CiContentError
from gerrit_workflow_tools.core.comment_target import MissingChangeIdError
from gerrit_workflow_tools.core.config import ConfigError, Settings
from gerrit_workflow_tools.core.gerrit.change_resolution import (
    ChangeAmbiguousError,
    ChangeResolutionError,
    Resolution,
    StackCommit,
    format_local_sibling_note,
    format_resolution_note,
)
from gerrit_workflow_tools.core.gerrit.rest import GerritApiError, GerritRest
from gerrit_workflow_tools.core.git_run import GitError, UnknownRevisionError
from gerrit_workflow_tools.core.run_context import RunContext
from gerrit_workflow_tools.core.upstream import NeedsUpstreamError
from gerrit_workflow_tools.core.upstream_interactive import ask_for_needed_upstream, stdin_is_terminal
from gerrit_workflow_tools.core.usage import UsageError
from gerrit_workflow_tools.summary_highlight import SummaryHighlighter, build_summary_highlighter


_T = TypeVar("_T")

HELP_JSON = "Write machine-readable JSON to stdout."
HELP_COLOR = "Colorize output: always, auto, or never."
HELP_HYPERLINKS = "Emit OSC 8 terminal hyperlinks: always, auto, or never."
HELP_DEBUG_LOG = (
    "Log diagnostics to stderr (git commands, outcomes, resolved refs/URLs, decisions, Gerrit API "
    "response bodies, and a high-level request timing summary)."
)


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
    """Required git configuration is missing (``gerrit.webUrl``, credentials)."""
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


# Ordered longest-subclass-first: ChangeAmbiguousError and MissingChangeIdError derive from ChangeResolutionError,
# and ConfigError from ValueError, so the more specific entry has to be matched first.
_FAILURE_EXITS: tuple[tuple[type[BaseException], ExitCode, str], ...] = (
    (ChangeAmbiguousError, ExitCode.AMBIGUOUS, "error"),
    (MissingChangeIdError, ExitCode.MISSING_CHANGE_ID, "error"),
    (ChangeResolutionError, ExitCode.NOT_FOUND, "error"),
    (GerritApiError, ExitCode.GERRIT, "gerrit error"),
    (ConfigError, ExitCode.CONFIG, "error"),
    (UsageError, ExitCode.USAGE, "error"),
    (UnknownRevisionError, ExitCode.NOT_FOUND, "error"),
    # A walk with nowhere left to go: reported without an "error:" prefix, since scripts loop on it.
    (NoEditAttentionError, ExitCode.NO_EDIT_ATTENTION, ""),
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
                    return _report_failure(error)
                if not answer:
                    return int(ExitCode.REFUSED)
            except Exception as error:  # pylint: disable=broad-exception-caught
                return _report_failure(error)
    finally:
        _MAY_ASK_FOR_UPSTREAM.reset(scope)
        print_call_trace_summary()


def _report_failure(error: Exception) -> int:
    """Print a known failure and return its exit code; re-raise anything else (a bug)."""
    failure = failure_exit(error)
    if failure is None:
        raise error
    code, prefix = failure
    sys.stdout.flush()  # What the command did show comes first, the reason it stopped last.
    print(f"{prefix}: {error}" if prefix else str(error), file=sys.stderr)
    return int(code)


def print_note(note: str | None) -> None:
    """Print a transparency note, dimmed, on stderr so stdout stays machine-readable."""
    if note:
        print(color_text(note, ANSI_DIM), file=sys.stderr)


def print_resolution_note(resolution: Resolution | None) -> None:
    """Print how *resolution* narrowed, when Gerrit was consulted and it did, and any local sibling."""
    if resolution is not None:
        print_note(format_resolution_note(resolution))
        print_note(format_local_sibling_note(resolution))


def print_stack_commit_notes(target: StackCommit) -> None:
    """:func:`print_resolution_note` for a changeish resolved to a stack commit."""
    if target.resolution is not None:
        print_note(format_resolution_note(target.resolution))
    print_note(target.note)


def ask_choice(prompt: str, answers: Mapping[str, _T], *, hint: str | None = None, on_eof: _T | None = None) -> _T:
    """Ask until the lower-cased reply is a key of *answers*; return what it maps to.

    End of input or Ctrl-C returns *on_eof* when one is given, and otherwise propagates.
    """
    while True:
        try:
            reply = input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            if on_eof is None:
                raise
            print()
            return on_eof
        if reply in answers:
            return answers[reply]
        if hint:
            print(hint, file=sys.stderr)


def read_message(messages: list[str] | None, file: str | None) -> str:
    """Message text from repeated ``-m`` (one paragraph each) or ``-F FILE`` (``-``: stdin), stripped."""
    if file is not None:
        text = sys.stdin.read() if file == "-" else Path(file).read_text(encoding="utf-8")
    else:
        text = "\n\n".join(messages or [])
    return text.strip()


def confirm(question: str, *, default: bool) -> bool:
    """Ask a yes/no *question*. Enter takes *default*; anything else unknown asks again."""
    suffix = "[Y/n]" if default else "[y/N]"
    answers = {"y": True, "yes": True, "n": False, "no": False, "": default}
    return ask_choice(f"{question} {suffix}: ", answers, on_eof=False)


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


_CHANGEISH_FORMS = "Change-Id (I…), change:<n>, triplet, refs/changes/… ref or Gerrit URL"


def changeish_help(what: str, *, change_only: bool = False) -> str:
    """Help for a **changeish** argument; one wording, so every command tells the same truth.

    *change_only* is for arguments that can only ever name a Gerrit change (``ger checkout``,
    ``ger review``): a bare number is then that change. Everywhere else it is a git revision first.
    """
    if change_only:
        return f"{what}: a change number, {_CHANGEISH_FORMS}."
    return f"{what}: a git revision (a bare number too: write change:<n> for a change), {_CHANGEISH_FORMS}."


def add_change_option(parser: argparse.ArgumentParser | argparse._ArgumentGroup, help_text: str) -> None:
    """Register ``--change CHANGEISH``: it only ever names a Gerrit change, so a bare number is one.

    One registration for every command, so ``--change 692`` means change 692 everywhere.
    """
    parser.add_argument(
        "--change",
        metavar="CHANGEISH",
        default=None,
        type=as_change_only,
        help=f"{help_text} A change number, {_CHANGEISH_FORMS}.",
    )


def add_output_format_args(parser: argparse.ArgumentParser, *, ai_view: str | None = None) -> None:
    """Register mutually exclusive ``--json`` / ``--format`` / ``--ai``; read the choice with :func:`output_format`.

    Every command with ``--json`` takes ``--ai`` (``--format ai``): text for a model to read, never a prompt.
    *ai_view* says what that is when the command has a view of its own for it; ``"markdown"`` makes it
    the command's Markdown view and offers ``--format markdown`` too. Without one, ``ai`` is the human
    output with colour and hyperlinks off.
    """
    fmt = parser.add_mutually_exclusive_group()
    fmt.add_argument("--json", action="store_true", dest="json_", help=HELP_JSON)
    markdown = ai_view == "markdown"
    choices = ("human", "markdown", "ai") if markdown else ("human", "ai")
    view = "the Markdown view" if markdown else ai_view or "the human output without colour or hyperlinks"
    fmt.add_argument(
        "--format",
        choices=choices,
        dest="format_",
        help=f"Output format (default: human). 'ai': {view}, for a model; it never prompts.",
    )
    fmt.add_argument("--ai", action="store_const", const="ai", dest="format_", help="Alias for --format ai.")
    parser.set_defaults(ai_view_="markdown" if markdown else "ai")


def output_format(args: argparse.Namespace) -> str:
    """``json``, ``markdown``, ``ai`` or ``human``, from the flags :func:`add_output_format_args` registered."""
    if getattr(args, "json_", False):
        return "json"
    fmt = getattr(args, "format_", None) or "human"
    return str(args.ai_view_) if fmt == "ai" else fmt


def may_prompt(args: argparse.Namespace) -> bool:
    """Whether a command may ask: human output on a terminal. ``--json`` and ``--ai`` output never prompts."""
    return output_format(args) == "human" and stdin_is_terminal()


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

    Pass *verbose_help* when a command uses ``--verbose`` for richer output. Without it
    ``-v`` does nothing, so it is accepted (scripts may pass it) but not advertised: no help
    line, no completion. With *verbose_count*, ``-v`` may be repeated (e.g. ``-vv``);
    ``args.verbose`` is an ``int`` (0 when omitted).
    """
    v_help = verbose_help or argparse.SUPPRESS
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


# Set by `ger --refresh` (cli_ger) for this process and the git-run editors it starts,
# such as the rebase enricher. Read here, once per run, and nowhere below.
REFRESH_ENV = "GER_CACHE_REFRESH"


ASSUME_YES_ENV = "GER_ASSUME_YES"


def assume_yes() -> bool:
    """Whether ``GER_ASSUME_YES`` answers every confirmation with yes, as ``-y`` does (agent sandboxes)."""
    return os.environ.get(ASSUME_YES_ENV, "").strip().lower() in ("1", "true", "yes")


def refresh_requested() -> bool:
    """Whether this process runs under ``ger --refresh``."""
    return os.environ.get(REFRESH_ENV, "").strip().lower() in ("1", "true", "yes")


def init_cli_runtime(
    args: argparse.Namespace, *, repository: bool = True, gerrit: GerritRest | None = None
) -> tuple[RunContext, SummaryHighlighter]:
    """Configure logging/color from parsed *args*; return the **run context** and the summary highlighter.

    Commands without ``--debug-log`` or ``--color`` get logging off and color on ``auto``.
    Outside a git repository a command that needs one (*repository*, the default) fails here,
    the same way for every command, before it can misreport what it would have looked up.

    This is where a command's :class:`Settings` snapshot is taken: once, at the entry
    point, from a single ``git config --list --show-scope``. Everything below receives it as an
    argument rather than reading configuration again. *gerrit* replaces the configured host
    (tests); ``ger --refresh`` reaches the run's service from here.
    """

    ensure_utf8_stdio()
    configure_logging(getattr(args, "debug_log", False))
    cwd = Path.cwd()
    plain = getattr(args, "format_", None) == "ai"  # Text for a model, Markdown or not: no colour, no links.
    init_color_mode(color="never" if plain else getattr(args, "color", "auto"))
    init_hyperlink_mode(hyperlinks="never" if plain else getattr(args, "hyperlinks", "auto"))
    settings = Settings.from_cwd(cwd)
    _MAY_ASK_FOR_UPSTREAM.set(may_prompt(args) and not getattr(args, "dry_run", False))
    if repository and not settings.in_repository:
        raise GitError("not a git repository; run ger inside a clone")
    run = RunContext.open(cwd, settings=settings, gerrit=gerrit, refresh=refresh_requested())
    return run, build_summary_highlighter(settings)
