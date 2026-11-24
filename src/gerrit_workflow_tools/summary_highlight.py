"""Highlight ready-boundary and warning patterns in commit summaries."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.stack_context import stack_project
from gerrit_workflow_tools.core.git_state import repo_toplevel
from gerrit_workflow_tools.core.ready_strategy import blocked_shas_for_stack
from gerrit_workflow_tools.core.run_context import RunContext
from gerrit_workflow_tools.core.stack import Commit, get_stack_snapshot
from gerrit_workflow_tools.render.style import ANSI_RED, ANSI_YELLOW, color_text, is_color_enabled


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
    root: Path | None = None,
    commits: list[Commit] | None = None,
    project: str = "",
    web_base: str | None = None,
) -> SummaryHighlighter:
    """Build a highlighter using the same ready boundary rules as ``ger push``."""

    blocked = frozenset[str]()
    if commits:
        blocked = blocked_shas_for_stack(
            root,
            project=project,
            commits=commits,
            stop_pattern=settings.stop_pattern,
            settings=settings,
            web_base=web_base,
        )

    return SummaryHighlighter(blocked_shas=blocked, warning_re=_warning_re(settings))


def _warning_re(settings: Settings) -> re.Pattern[str] | None:
    """``ger.warningPattern`` compiled, or ``None`` when unset or not a valid regex."""
    warning = settings.warning_pattern
    if not warning:
        return None
    try:
        return re.compile(warning, re.IGNORECASE)
    except re.error:
        return None


def run_summary_highlighter(
    run: RunContext,
    *,
    commits: list[Commit] | None = None,
    project: str = "",
    blocked: frozenset[str] | None = None,
) -> SummaryHighlighter:
    """The run's highlighter: its warning pattern, and the ready boundary of *commits* when given.

    *blocked* is that boundary's verdict when the caller already has it (``StackOverview.blocked``).
    """
    settings = run.settings
    if blocked is not None:
        return SummaryHighlighter(blocked_shas=blocked, warning_re=_warning_re(settings))
    return build_summary_highlighter(
        settings,
        root=run.repo.root if commits else None,
        commits=commits,
        project=project,
        web_base=settings.gerrit_web_url,
    )


def branch_summary_highlighter(
    cwd: Path, branch: str, *, first_parent: bool = True, settings: Settings
) -> SummaryHighlighter:
    """Highlighter aligned with the ready boundary of *branch*'s local stack."""
    snap = get_stack_snapshot(cwd, branch, head=branch, first_parent=first_parent, settings=settings)
    return build_summary_highlighter(
        settings,
        root=repo_toplevel(cwd),
        commits=list(snap.commits),
        project=stack_project(cwd, branch, settings=settings),
        web_base=settings.gerrit_web_url,
    )
