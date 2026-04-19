from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from gerrit_workflow_tools.cli_style import ANSI_RED, ANSI_YELLOW, color_text, is_color_enabled
from gerrit_workflow_tools.config import stop_patterns, warning_patterns


@dataclass(frozen=True)
class SummaryHighlighter:
    """Highlight stop/warning pattern matches in commit summaries."""

    combined_re: re.Pattern[str] | None

    def highlight(self, summary: str) -> str:
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


def _named_groups(patterns: list[str], prefix: str) -> list[str]:
    groups: list[str] = []
    for i, pat in enumerate(patterns):
        try:
            re.compile(pat)
        except re.error:
            continue
        groups.append(f"(?P<{prefix}_{i}>{pat})")
    return groups


def build_summary_highlighter(cwd: Path | str | None) -> SummaryHighlighter:
    """Build a highlighter where stop-pattern matches have precedence over warnings."""
    stop_groups = _named_groups(stop_patterns(cwd), "stop")
    warning_groups = _named_groups(warning_patterns(cwd), "warning")
    if not stop_groups and not warning_groups:
        return SummaryHighlighter(combined_re=None)
    combined = "|".join([*stop_groups, *warning_groups])
    return SummaryHighlighter(combined_re=re.compile(combined, re.IGNORECASE))
