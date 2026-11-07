"""Push-options history: what was pushed with last time, keyed by Gerrit host and project, and Up/Down recall.

Stored lines never include strategy keywords (ADR-0005). The prompt in :mod:`push_input_prompt` reads this;
``ger push`` records a line once the push has succeeded.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.paths import push_options_history_path
from gerrit_workflow_tools.core.gerrit.stack_context import stack_project
from gerrit_workflow_tools.core.push import PushPlan
from gerrit_workflow_tools.push_input_line import PushLineState, format_canonical, parse


_HISTORY_LIMIT = 20


def _history_file(web_base: str, project: str) -> Path:
    return push_options_history_path(web_base, project)


def load_push_options_history(*, web_base: str | None, project: str | None) -> list[str]:
    """Return stored history lines for *web_base*+*project*, newest first.

    Returns an empty list when host/project identity is missing (no global
    fallback). Lines never include strategy keywords.
    """
    if not web_base or not project:
        return []
    try:
        raw = _history_file(web_base, project).read_text(encoding="utf-8")
    except OSError:
        return []
    return [line for line in raw.splitlines() if line.strip()]


def prepend_push_options_history(
    line: str,
    *,
    web_base: str | None,
    project: str | None,
) -> None:
    """Prepend a history line (strategy stripped; dedupe; cap at :data:`_HISTORY_LIMIT`).

    No-op when host/project identity is missing.
    """
    if not web_base or not project:
        return
    # Persist reviewers/topic/wip/private only — never strategy.
    canonical = format_canonical(parse(line).state, include_strategy=False).strip()
    recent = [entry for entry in load_push_options_history(web_base=web_base, project=project) if entry != canonical]
    updated = ([canonical, *recent] if canonical else recent)[:_HISTORY_LIMIT]
    path = _history_file(web_base, project)
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        path.write_text("\n".join(updated) + ("\n" if updated else ""), encoding="utf-8")


class HistoryCursor:
    """Up/Down recall over *entries* (newest first) with the typed line as the bottom of the list.

    History is only a suggestion: a recalled entry that is then edited stops being a history
    slot and becomes the current line, so the next Up starts again from the newest entry and
    Down returns to the edited line.
    """

    def __init__(self, entries: list[str], current: str = "") -> None:
        self._entries = entries
        self._current = current
        self._pos = -1  # -1: the current line; n >= 0: entries[n]

    def _settle(self, text: str) -> None:
        """Take *text* as the current line when it is not the entry being shown."""
        if self._pos == -1 or text != self._entries[self._pos]:
            self._current = text
            self._pos = -1

    def up(self, text: str) -> str:
        self._settle(text)
        if self._pos + 1 < len(self._entries):
            self._pos += 1
            return self._entries[self._pos]
        return text

    def down(self, text: str) -> str:
        self._settle(text)
        if self._pos == -1:
            return text
        self._pos -= 1
        return self._current if self._pos == -1 else self._entries[self._pos]


def push_options_history_identity(
    cwd: Path | None,
    *,
    settings: Settings,
) -> tuple[str | None, str | None]:
    """Return ``(web_base, project)`` for push-options history, or ``(None, None)``."""
    web_base = settings.gerrit_web_url
    project = stack_project(cwd, settings=settings) if cwd is not None else settings.gerrit_project
    if not web_base or not project:
        return None, None
    return web_base, project


def remember_push_options(cwd: Path, plan: PushPlan, *, settings: Settings) -> None:
    """Save the options of *plan* to history, now that a push has shown they work."""
    options = plan.options
    state = PushLineState(list(options.reviewers), options.topic, options.wip, options.private)
    web_base, project = push_options_history_identity(cwd, settings=settings)
    prepend_push_options_history(format_canonical(state, include_strategy=False), web_base=web_base, project=project)
