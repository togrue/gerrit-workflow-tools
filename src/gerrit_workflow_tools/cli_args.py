"""Shared argparse groups for ``ger`` commands: one definition per flag several commands take.

A flag two commands take is registered here, once, so it means the same thing with the same
help everywhere (``tests/test_architecture.py`` pins that).

Help text style (``help=`` on parsers and arguments):

- Imperative mood, sentence case, and end each string with a period (consistent).
- When a flag only affects package logging, phrase it as "Log … to stderr".
- Shared flag text lives in ``HELP_*`` constants in this module; CLIs should use them.
- Use :func:`add_verbose_and_debug_log_args` for ``-v``/``--verbose`` and ``--debug-log``
  (diagnostic logging to stderr); do not use ``--verbose`` for logging.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from gerrit_workflow_tools.core.changeish import as_change_only
from gerrit_workflow_tools.core.votes import VOTE_HELP


HELP_JSON = "Write machine-readable JSON to stdout."
HELP_COLOR = "Colorize output: always, auto, or never."
HELP_HYPERLINKS = "Emit OSC 8 terminal hyperlinks: always, auto, or never."
HELP_REFRESH = (
    "Ask Gerrit again instead of serving what the cache holds (its trust window), and refetch CI logs. "
    "Same as `ger --refresh <command>`."
)
HELP_DEBUG_LOG = "Log diagnostics to stderr: git commands, Gerrit calls and responses, decisions, and their timing."


HELP_BRANCH = "Work on local branch NAME: its local stack and upstream (default: the working branch)."


def add_branch_arg(parser: argparse.ArgumentParser | argparse._ArgumentGroup) -> None:
    """Register ``--branch NAME``: the local branch whose **local stack** the command works on."""
    parser.add_argument("--branch", metavar="NAME", default=None, help=HELP_BRANCH)


def add_stack_arg(parser: argparse.ArgumentParser | argparse._ArgumentGroup) -> None:
    """Register ``--stack``: every commit of the **local stack**, instead of the revisions named."""
    parser.add_argument(
        "--stack", action="store_true", help="Include all commits in the local stack (upstream_tip..HEAD)."
    )


HELP_YES = "Answer yes instead of asking; needed without a terminal (GER_ASSUME_YES=1 too)."
HELP_DRY_RUN = "Show what would be sent, and send nothing."


def _scoped(help_text: str, scope: str | None) -> str:
    """*help_text* for a flag that only applies with *scope* (``--publish``): ``With --publish: …``."""
    return f"With {scope}: {help_text[:1].lower()}{help_text[1:]}" if scope else help_text


def add_yes_arg(parser: argparse.ArgumentParser | argparse._ArgumentGroup, *, scope: str | None = None) -> None:
    """Register ``-y``/``--yes``: answer the command's confirmation with yes (``GER_ASSUME_YES=1`` too).

    *scope* names the flag it belongs to when only one mode of the command asks (``--publish``).
    """
    parser.add_argument("-y", "--yes", action="store_true", help=_scoped(HELP_YES, scope))


def add_dry_run_arg(parser: argparse.ArgumentParser | argparse._ArgumentGroup, *, scope: str | None = None) -> None:
    """Register ``--dry-run``: show what the command would send, and send nothing."""
    parser.add_argument("--dry-run", action="store_true", help=_scoped(HELP_DRY_RUN, scope))


def add_vote_arg(parser: argparse.ArgumentParser | argparse._ArgumentGroup, *, scope: str | None = None) -> None:
    """Register ``--vote LABEL=N`` (repeatable): the votes the command publishes; *scope* as for :func:`add_yes_arg`."""
    parser.add_argument("--vote", action="append", default=None, metavar="LABEL=N", help=_scoped(VOTE_HELP, scope))


def add_patchset_arg(parser: argparse.ArgumentParser | argparse._ArgumentGroup) -> None:
    """Register ``--patchset N``: which patch set of the change to take (default: the current one)."""
    parser.add_argument("--patchset", type=int, default=None, metavar="N", help="Take patchset N (default: current).")


def add_on_off_arg(parser: argparse.ArgumentParser | argparse._ArgumentGroup, flag: str, *, help_text: str) -> None:
    """Register ``--flag`` and ``--no-flag`` for a switch a config key (or git) can turn on by default.

    The value is ``None`` when neither is given, so the command reads the default then:
    ``args.x if args.x is not None else settings…``. The one idiom for such a pair.
    """
    parser.add_argument(flag, action=argparse.BooleanOptionalAction, default=None, help=help_text)


def add_message_args(parser: argparse.ArgumentParser) -> argparse._ArgumentGroup:
    """Register ``-m``/``--message`` (repeatable) and ``-F``/``--file`` as a "message" group.

    Read them with :func:`read_message`.
    """
    text = parser.add_argument_group("message")
    text.add_argument("-m", "--message", action="append", default=None, help="Message; repeat for paragraphs.")
    text.add_argument("-F", "--file", metavar="FILE", default=None, help="Read the message from FILE ('-': stdin).")
    return text


def read_message(messages: list[str] | None, file: str | None) -> str:
    """Message text from repeated ``-m`` (one paragraph each) or ``-F FILE`` (``-``: stdin), stripped."""
    if file is not None:
        text = sys.stdin.read() if file == "-" else Path(file).read_text(encoding="utf-8")
    else:
        text = "\n\n".join(messages or [])
    return text.strip()


def add_follow_merges_args(parser: argparse.ArgumentParser) -> None:
    """Register ``--follow-merges`` (used by commands that display a local stack)."""
    parser.add_argument(
        "--follow-merges",
        action="store_true",
        default=False,
        help="Traverse all commit parents (including merge commits) instead of only the first-parent chain. "
        "By default only the first-parent chain is shown, matching Gerrit's relation-chain semantics.",
    )


_CHANGEISH_FORMS = "Change-Id (I…), change:<n> (or c<n>, '#<n>'), triplet, refs/changes/… ref or Gerrit URL"


def changeish_help(what: str, *, change_only: bool = False) -> str:
    """Help for a **changeish** argument; one wording, so every command tells the same truth.

    *change_only* is for arguments that can only ever name a Gerrit change (``ger checkout``,
    ``ger review``): a bare number is then that change. Everywhere else it is a git revision first.
    """
    if change_only:
        return f"{what}: a change number, {_CHANGEISH_FORMS}."
    return f"{what}: a git revision (<n> and c<n> too, else the change), {_CHANGEISH_FORMS}."


def add_change_option(
    parser: argparse.ArgumentParser | argparse._ArgumentGroup, help_text: str, *, repeatable: bool = False
) -> None:
    """Register ``--change CHANGEISH``: it only ever names a Gerrit change, so a bare number is one.

    One registration for every command, so ``--change 692`` means change 692 everywhere.
    *repeatable* collects every ``--change`` in a list (``None`` without one)."""
    parser.add_argument(
        "--change",
        metavar="CHANGEISH",
        default=None,
        action="append" if repeatable else "store",
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


def add_color_args(parser: argparse.ArgumentParser) -> None:
    """Register shared color-output and hyperlink flags."""
    for flag, help_text in (("--color", HELP_COLOR), ("--hyperlinks", HELP_HYPERLINKS)):
        parser.add_argument(flag, choices=("always", "auto", "never"), default="auto", metavar="WHEN", help=help_text)


def add_verbose_and_debug_log_args(
    parser: argparse.ArgumentParser,
    *,
    verbose_help: str | None = None,
    verbose_count: bool = False,
    refresh: bool = True,
) -> None:
    """Register ``-v``/``--verbose``, ``--debug-log`` and, for a command that reads Gerrit (*refresh*), ``--refresh``.

    Pass *verbose_help* when a command uses ``--verbose`` for richer output. Without it
    ``-v`` does nothing, so it is accepted (scripts may pass it) but not advertised: no help
    line, no completion. With *verbose_count*, ``-v`` may be repeated (e.g. ``-vv``);
    ``args.verbose`` is an ``int`` (0 when omitted). ``--debug-log`` says the same everywhere.
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
        help=HELP_DEBUG_LOG,
    )
    if refresh:
        # `ger --refresh <command>` works anywhere on the line (cli_ger takes it out before the command's
        # parser runs); it is listed here so each command's --help says so.
        parser.add_argument("--refresh", action="store_true", help=HELP_REFRESH)
