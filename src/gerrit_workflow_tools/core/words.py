"""How ger says a count: ``1 draft``, ``2 drafts``. One way, for core's messages and render's lines alike.

Never ``draft(s)``: the count is known, so the noun agrees with it.
"""

from __future__ import annotations


def plural(count: int, noun: str, plural_form: str | None = None) -> str:
    """*noun* as it goes with *count*: itself for one, else *plural_form* (default: *noun* + ``s``)."""
    return noun if count == 1 else plural_form or f"{noun}s"


def counted(count: int, noun: str, plural_form: str | None = None) -> str:
    """*count* and its *noun*, agreeing: ``1 commit``, ``0 commits``, ``3 commits``."""
    return f"{count} {plural(count, noun, plural_form)}"
