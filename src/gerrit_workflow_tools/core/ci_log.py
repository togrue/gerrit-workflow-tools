"""A CI log as clean lines: what every finder reads, and the shapes finders share.

Kept apart from the finders so each finder module depends on this one, not on its siblings.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

from gerrit_workflow_tools.core.ci_content import CiFinding


Finder = Callable[[Sequence[str], set[int]], list[CiFinding]]
"""``(lines, claimed) -> findings``; a finder adds the indices it used to *claimed*."""

PATH = r"(?:[A-Za-z]:)?[^\s:]+"
"""Regex fragment for a file path in a diagnostic: no spaces, an optional drive letter."""

DETAIL_LINES = 8
"""Lines of excerpt a finding's ``detail`` keeps."""

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_TIMESTAMP = re.compile(r"^\[?(?:\d{4}-\d\d-\d\d[T ])?\d\d:\d\d:\d\d(?:[.,]\d+)?Z?\]?\s+")


def clean_log_lines(text: str) -> list[str]:
    """Log lines without ANSI escapes, carriage returns or Jenkins timestamper prefixes."""
    return [_TIMESTAMP.sub("", _ANSI.sub("", line.rstrip("\r"))) for line in text.splitlines()]
