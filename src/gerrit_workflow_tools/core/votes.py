"""The vote grammar: ``+1``, ``CR+1``, ``V=-1``, ``Code-Review=+2``. Pure syntax, no I/O.

One reading for every command that votes (``ger review --vote``, ``ger reply --publish --vote``).
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from gerrit_workflow_tools.core.usage import UsageError


_VOTE_RE = re.compile(r"^([A-Za-z][\w-]*)\s*(?:=\s*([+-]?\d+)|([+-]\d+))$")
_BARE_RE = re.compile(r"^[+-]\d+$")
_LABEL_ALIASES = {"cr": "Code-Review", "v": "Verified"}
DEFAULT_LABEL = "Code-Review"
"""The label a bare ``+1`` votes."""

VOTE_HELP = "Vote, e.g. +1 (Code-Review), CR-1, V=+1 (CR: Code-Review, V: Verified)."


def parse_votes(raw: list[str] | None) -> dict[str, int]:
    """``LABEL=N`` / ``LABEL+N`` / ``LABEL-N`` values as ``{label: value}``; :class:`UsageError` on one that is not."""
    votes: dict[str, int] = {}
    for item in raw or ():
        if _BARE_RE.match(item.strip()):
            votes[DEFAULT_LABEL] = int(item)
            continue
        match = _VOTE_RE.match(item.strip())
        if match is None:
            raise UsageError(f"not a vote: {item!r} (use +N or -N for Code-Review, LABEL=N, LABEL+N or LABEL-N)")
        label = _LABEL_ALIASES.get(match.group(1).lower(), match.group(1))
        votes[label] = int(match.group(2) or match.group(3))
    return votes


def format_vote(label: str, value: int) -> str:
    """``Code-Review+1``, ``Verified-1``, ``Code-Review 0``: as Gerrit writes a vote."""
    return f"{label}{value:+d}" if value else f"{label} 0"


def format_votes(votes: Mapping[str, int]) -> str:
    """``Code-Review+1, Verified-1``."""
    return ", ".join(format_vote(label, value) for label, value in votes.items())


def votes_ruled_out(votes: Mapping[str, int], permitted: Mapping[str, tuple[int, ...]]) -> list[str]:
    """The *votes* that *permitted* (``ChangeView.permitted_votes``) rules out, each with what you may give.

    Empty when *permitted* is empty: Gerrit did not say, so nothing is ruled out here.
    """
    if not permitted:
        return []
    ruled_out: list[str] = []
    for label, value in votes.items():
        allowed = permitted.get(label, ())
        if value not in allowed:
            may = ", ".join(f"{v:+d}" if v else "0" for v in allowed)
            ruled_out.append(
                f"{format_vote(label, value)} (you may give {may})" if may else f"{label} (not yours to vote)"
            )
    return ruled_out
