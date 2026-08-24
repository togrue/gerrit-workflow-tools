"""Highlight ready-boundary and warning patterns in commit summaries."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from gerrit_workflow_tools.cli_style import ANSI_RED, ANSI_YELLOW, color_text, is_color_enabled
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.ready_strategy import ReadyCommitRow, blocked_shas_for_stack


@dataclass(frozen=True)
class SummaryHighlighter:
    """Highlight non-pushable commits (ready boundary) and warning-pattern matches."""

    blocked_shas: frozenset[str]
    warning_re: re.Pattern[str] | None

    def highlight(self, summary: str, *, sha: str | None = None) -> str:
        """Colorize summaries on the non-pushable tail (red) or warning regex (yellow)."""

        if not summary or not is_color_enabled():
            return summary
        if sha and sha in self.blocked_shas:
            return color_text(summary, ANSI_RED)
        if self.warning_re is None:
            return summary
        out: list[str] = []
        last = 0
        for match in self.warning_re.finditer(summary):
            start, end = match.span()
            if start == end:
                continue
            if start > last:
                out.append(summary[last:start])
            out.append(color_text(summary[start:end], ANSI_YELLOW))
            last = end
        if last < len(summary):
            out.append(summary[last:])
        return "".join(out)


def build_summary_highlighter(
    settings: Settings,
    *,
    cwd: Path | str | None = None,
    commits: list[ReadyCommitRow] | None = None,
    project: str = "",
    web_base: str | None = None,
) -> SummaryHighlighter:
    """Build a highlighter using the same ready boundary rules as ``ger push``."""

    blocked = frozenset[str]()
    if commits and cwd is not None:
        blocked = blocked_shas_for_stack(
            cwd,
            project=project,
            commits=commits,
            stop_pattern=settings.stop_pattern,
            settings=settings,
            web_base=web_base,
        )

    warning_re: re.Pattern[str] | None = None
    warning = settings.warning_pattern
    if warning:
        try:
            warning_re = re.compile(warning, re.IGNORECASE)
        except re.error:
            warning_re = None

    return SummaryHighlighter(blocked_shas=blocked, warning_re=warning_re)
