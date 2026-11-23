"""The vote grammar: ``CR+1``, ``V=-1``, ``Code-Review=+2``. Pure syntax, no I/O.

One reading for every command that votes (``ger review --vote``, ``ger reply --publish --vote``).
"""

from __future__ import annotations

import re

from gerrit_workflow_tools.core.usage import UsageError


_VOTE_RE = re.compile(r"^([A-Za-z][\w-]*)\s*(?:=\s*([+-]?\d+)|([+-]\d+))$")
_LABEL_ALIASES = {"cr": "Code-Review", "v": "Verified"}

VOTE_HELP = "Vote, e.g. CR+1, V=-1 (CR: Code-Review, V: Verified)."


def parse_votes(raw: list[str] | None) -> dict[str, int]:
    """``LABEL=N`` / ``LABEL+N`` / ``LABEL-N`` values as ``{label: value}``; :class:`UsageError` on one that is not."""
    votes: dict[str, int] = {}
    for item in raw or ():
        match = _VOTE_RE.match(item.strip())
        if match is None:
            raise UsageError(f"not a vote: {item!r} (use LABEL=N, LABEL+N or LABEL-N)")
        label = _LABEL_ALIASES.get(match.group(1).lower(), match.group(1))
        votes[label] = int(match.group(2) or match.group(3))
    return votes
