"""Highlight stop/warning patterns in commit summaries."""

from __future__ import annotations

import re
from dataclasses import dataclass

from gerrit_workflow_tools.cli_style import ANSI_RED, ANSI_YELLOW, color_text, is_color_enabled
from gerrit_workflow_tools.core.config import Settings


@dataclass(frozen=True)
class SummaryHighlighter:
    """Highlight stop/warning pattern matches in commit summaries."""

    combined_re: re.Pattern[str] | None

    def highlight(self, summary: str) -> str:
        """Colorize summary segments that match stop/warning regex groups."""

        if not summary or self.combined_re is None or not is_color_enabled():
            return summary
        out: list[str] = []
        last = 0
        for match in self.combined_re.finditer(summary):
            start, end = match.span()
            if start == end:
                continue
            if start > last:
                out.append(summary[last:start])
            if match.lastgroup and match.lastgroup.startswith("stop_"):
                out.append(color_text(summary[start:end], ANSI_RED))
            else:
                out.append(color_text(summary[start:end], ANSI_YELLOW))
            last = end
        if last < len(summary):
            out.append(summary[last:])
        return "".join(out)


def build_summary_highlighter(settings: Settings) -> SummaryHighlighter:
    """Build a highlighter where stop-pattern matches have precedence over warnings."""
    groups: list[str] = []
    stop = settings.stop_pattern
    if stop:
        re.compile(stop)
        groups.append(f"(?P<stop_0>{stop})")
    warning = settings.warning_pattern
    if warning:
        re.compile(warning)
        groups.append(f"(?P<warning_0>{warning})")
    if not groups:
        return SummaryHighlighter(combined_re=None)
    combined = "|".join(groups)
    return SummaryHighlighter(combined_re=re.compile(combined, re.IGNORECASE))
