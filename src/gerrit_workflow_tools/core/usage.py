"""The command cannot run as asked: one failure type, so every command exits ``USAGE`` for it."""

from __future__ import annotations


class UsageError(ValueError):
    """A bad argument, or a state the command refuses to start from.

    Covers what the user changes and then simply runs again: an invalid option value, a
    prompt with no terminal to answer it, nothing staged to commit, a work tree a history
    rewrite would trample. The message says what to do; ``cli_common`` maps it to ``USAGE``.
    """
