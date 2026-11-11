"""Interactive helpers for configuring branch upstream when missing."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TextIO

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.git_run import git
from gerrit_workflow_tools.core.git_state import is_detached_head
from gerrit_workflow_tools.core.upstream import (
    branch_has_upstream,
    local_branch_exists,
    set_branch_upstream,
    upstream_choice,
)


logger = logging.getLogger(__name__)


def _is_windows_console(stream: TextIO) -> bool:
    """Ask Windows itself: ``isatty`` is also true for ``NUL``, which ``/dev/null`` maps to."""
    import ctypes
    import msvcrt

    try:
        handle = msvcrt.get_osfhandle(stream.fileno())
    except (OSError, ValueError, AttributeError):
        return True
    return bool(ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(ctypes.c_ulong())))


def stdin_is_terminal() -> bool:
    """True when a prompt on stdin can be answered."""
    return sys.stdin.isatty() and (sys.platform != "win32" or _is_windows_console(sys.stdin))


def prompt_toolkit_can_run() -> bool:
    """True when a prompt_toolkit prompt can start: on Windows it also needs stdout to be a console."""
    return stdin_is_terminal() and (sys.platform != "win32" or _is_windows_console(sys.stdout))


def prompt_upstream_abbrev_interactive(cwd: Path | str | None, branch: str, *, settings: Settings) -> str | None:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import WordCompleter

    choice_ = upstream_choice(cwd, branch, settings=settings)
    candidates = choice_.candidates
    default = choice_.suggested or (candidates[0] if candidates else "")
    completer = WordCompleter(candidates, ignore_case=False, sentence=True)
    session: PromptSession[str] = PromptSession()
    print(
        f"No upstream configured for branch {branch!r}.",
        file=sys.stderr,
    )
    print("Set upstream (TAB to complete, Enter on empty line to abort).", file=sys.stderr)
    try:
        choice = session.prompt(
            f"Set upstream for {branch!r}: ",
            default=default,
            completer=completer,
            complete_while_typing=True,
        )
    except (EOFError, KeyboardInterrupt):
        print("Aborted.", file=sys.stderr)
        return None
    chosen = choice.strip()
    if not chosen:
        print("Aborted.", file=sys.stderr)
        return None
    p = git("rev-parse", "--verify", "--quiet", f"refs/remotes/{chosen}", cwd=cwd, check=False)
    if p.returncode != 0:
        print(f"error: {chosen!r} is not a remote-tracking branch. Run `git fetch` and try again.", file=sys.stderr)
        return None
    return chosen


def missing_upstream_error_message(branch: str) -> str:
    return (
        f"No upstream configured for branch {branch!r}.\n"
        "Set an upstream, e.g.:\n"
        "  git branch --set-upstream-to=<remote>/<branch>\n"
        "Fetch from your Gerrit remote first if the tracking branch is missing."
    )


def ensure_branch_upstream_interactive(cwd: Path | str | None, branch: str, *, settings: Settings) -> bool:
    if is_detached_head(cwd):
        return False
    if branch_has_upstream(cwd, branch):
        return True
    if not prompt_toolkit_can_run():
        return False
    choice = prompt_upstream_abbrev_interactive(cwd, branch, settings=settings)
    if not choice:
        return False
    set_branch_upstream(cwd, branch, choice)
    print(f"Upstream for {branch!r} set to {choice}.", file=sys.stderr)
    return True


def require_branch_upstream(cwd: Path | str | None, branch: str, *, settings: Settings) -> bool:
    """Return True when *branch* has upstream (possibly set via TTY prompt).

    When upstream is still missing and stdin is not a TTY, print setup guidance
    to stderr before returning False. Non-existent local branches are ignored so
    downstream git commands can report invalid refs.
    """
    if not local_branch_exists(cwd, branch):
        return True
    if branch_has_upstream(cwd, branch):
        return True
    if ensure_branch_upstream_interactive(cwd, branch, settings=settings):
        return True
    if not prompt_toolkit_can_run():
        print(f"error: {missing_upstream_error_message(branch)}", file=sys.stderr)
    return False
