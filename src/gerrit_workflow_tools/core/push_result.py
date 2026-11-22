"""What ``git push`` answered, and what ``ger push`` did after it. Does not print or prompt."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from gerrit_workflow_tools.core.push_reviewers import ReviewerApplyResult
from gerrit_workflow_tools.core.push_unchanged import StateUpdate, StateUpdateFailure


NOTHING_TO_PUSH = "nothing to push: Gerrit already has these commits"


@dataclass(frozen=True)
class PushResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""
    error_message: str | None = None
    reviewers: ReviewerApplyResult | None = None
    warning: str | None = None
    updated: tuple[StateUpdate, ...] = ()
    """What was set over REST on changes Gerrit already had at that commit."""
    update_failures: tuple[StateUpdateFailure, ...] = ()
    nothing_sent: bool = False
    """Gerrit took no new patch set: every commit was already on it, or it answered ``no new changes``."""

    @property
    def set_nothing(self) -> bool:
        """Nothing was sent, and nothing set over REST either: the push left Gerrit as it was."""
        return self.nothing_sent and not (self.updated or self.update_failures or self.reviewers)

    @property
    def gerrit_failed(self) -> bool:
        """Gerrit refused what the push sets over REST: reviewers, or options on changes it already had."""
        return bool(self.update_failures) or (self.reviewers is not None and not self.reviewers.ok)


def push_rejected_no_new_changes(stdout: str, stderr: str) -> bool:
    return "no new changes" in f"{stdout}{stderr}".lower()


_REMOTE_REJECTED = re.compile(r"\[remote rejected\][^\n(]*\((.*?)\)[ \t]*(?:\n|$)", re.S)


def gerrit_rejection(stderr: str) -> str | None:
    """One line with Gerrit's reason for refusing the push, from git's ``! [remote rejected] … (reason)``.

    The reason's lines are joined by ``; ``. ``None`` when git reports no rejection by Gerrit.
    """
    match = _REMOTE_REJECTED.search(stderr)
    lines = [line.strip() for line in match.group(1).splitlines() if line.strip()] if match else []
    return f"error: Gerrit rejected the push: {'; '.join(lines)}" if lines else None


def result_of(proc: Any) -> PushResult:
    """*proc*, a finished ``git push``, as a result: its code and its output, as text."""
    return PushResult(
        returncode=int(proc.returncode),
        stdout=_text(getattr(proc, "stdout", "")),
        stderr=_text(getattr(proc, "stderr", "")),
    )


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return ""
