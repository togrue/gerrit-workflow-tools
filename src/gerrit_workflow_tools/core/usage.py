"""The two ways a command declines to go on, each one failure type with one exit code.

:class:`UsageError` (``USAGE``): the request or the state is wrong, fix it and rerun.
:class:`Refusal` (``REFUSED``): ger will not go on without the user's decision.
"""

from __future__ import annotations


class UsageError(ValueError):
    """A bad argument, or a state the command refuses to start from.

    Covers what the user changes and then simply runs again: an invalid option value, a
    prompt with no terminal to answer it, nothing staged to commit, a work tree a history
    rewrite would trample. The message says what to do; ``cli_common`` maps it to ``USAGE``.
    """


class Refusal(RuntimeError):
    """ger will not go on without the user's decision: their commits, branches or edits are in the way.

    The user decides (``--force``, another change, by hand) rather than fixes a state and reruns;
    nothing was changed. ``cli_common`` maps it to ``REFUSED``, ``ger serve`` to 409.
    """
