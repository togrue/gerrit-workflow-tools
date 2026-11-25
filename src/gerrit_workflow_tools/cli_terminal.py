"""Asking at the terminal: whether the CLI can ask (is stdin a terminal, can a prompt_toolkit prompt start,
does ``GER_ASSUME_YES`` answer for the user) and the plain questions (:func:`ask_choice`, :func:`confirm`)."""

from __future__ import annotations

import io
import os
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, redirect_stdout
from typing import TextIO, TypeVar

from gerrit_workflow_tools.core.git_editor import run_git_pager


_T = TypeVar("_T")


def _is_windows_console(stream: TextIO) -> bool:
    """Ask Windows itself: ``isatty`` is also true for ``NUL``, which ``/dev/null`` maps to.

    Elsewhere ``isatty`` is trusted, so the answer is always yes. The ``sys.platform`` guard
    (not ``os.name``) is what lets mypy skip the Windows-only APIs below on other platforms.
    """
    if sys.platform != "win32":
        return True
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


ASSUME_YES_ENV = "GER_ASSUME_YES"


def assume_yes() -> bool:
    """Whether ``GER_ASSUME_YES`` answers every confirmation with yes, as ``-y`` does (agent sandboxes)."""
    return os.environ.get(ASSUME_YES_ENV, "").strip().lower() in ("1", "true", "yes")


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


def confirm(question: str, *, default: bool) -> bool:
    """Ask a yes/no *question*. Enter takes *default*; anything else unknown asks again."""
    suffix = "[Y/n]" if default else "[y/N]"
    answers = {"y": True, "yes": True, "n": False, "no": False, "": default}
    return ask_choice(f"{question} {suffix}: ", answers, on_eof=False)


@contextmanager
def paged(enabled: bool) -> Iterator[None]:
    """What is printed inside goes through git's pager (``git var GIT_PAGER``; ``LESS=FRX`` unless
    set, as git does) when *enabled* and stdout is a terminal; else straight out."""
    if not enabled or not sys.stdout.isatty():
        yield
        return
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        yield
    if not run_git_pager(buffer.getvalue()):
        sys.stdout.write(buffer.getvalue())
