"""The **step outcome**: what the last ger command that moves the checkout or sends it said at its end.

``ger rebase --continue|--skip|--abort`` and ``ger push`` run in a terminal, where a client that
started them sees nothing of how they ended. Each leaves its closing words here, in the worktree's
git directory (``ger-outcome.json``), and ``GET /stack`` serves them: the views say what happened
(a refusal too, a cancel), and know the step is over. One record, the newest; nothing else reads it.
"""

from __future__ import annotations

import contextlib
import json
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from gerrit_workflow_tools.core.git_run import commit_sha_or_none
from gerrit_workflow_tools.core.git_state import git_dir
from gerrit_workflow_tools.core.usage import UsageError


OUTCOME_FILE = "ger-outcome.json"


@dataclass(frozen=True)
class StepOutcome:
    """How a step ended, in ger's words."""

    command: str
    """The command as typed after ``ger``: ``rebase --abort``, ``push``."""
    ok: bool
    """It did what was asked; ``False`` for a refusal, a failure or a cancel."""
    text: str
    """The one line that says what happened: ``rebase aborted: s14 is at d66fc18 …, as before the rebase``."""
    more: tuple[str, ...] = ()
    """The lines after it: what was kept and where, what is gone, the ``next:`` step."""
    at: float = 0.0
    """When it ended (seconds since the epoch)."""
    head: str | None = None
    """``HEAD`` then: once it moved on, the outcome is history."""


def record_outcome(cwd: Path | str | None, command: str, text: str, *, ok: bool, more: Sequence[str] = ()) -> None:
    """Leave *text* as the outcome of *command*; a repository that cannot be written to keeps none."""
    repo_dir = git_dir(cwd)
    if repo_dir is None:
        return
    record = StepOutcome(command, ok, text, tuple(more), time.time(), commit_sha_or_none(cwd, "HEAD"))
    with contextlib.suppress(OSError):
        tmp = repo_dir / f"{OUTCOME_FILE}.tmp"
        tmp.write_text(json.dumps(record.__dict__), encoding="utf-8")
        tmp.replace(repo_dir / OUTCOME_FILE)


def last_outcome(repo_dir: Path | None) -> StepOutcome | None:
    """The outcome the last step left in *repo_dir*, or ``None``."""
    if repo_dir is None:
        return None
    try:
        raw = json.loads((repo_dir / OUTCOME_FILE).read_text(encoding="utf-8"))
        return StepOutcome(
            command=str(raw["command"]),
            ok=bool(raw["ok"]),
            text=str(raw["text"]),
            more=tuple(str(line) for line in raw.get("more", ())),
            at=float(raw.get("at", 0.0)),
            head=raw.get("head"),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


@contextlib.contextmanager
def refusal_recorded(cwd: Path | str | None, command: str) -> Iterator[None]:
    """Record a refusal (:class:`UsageError`) or an interrupt of *command* as its outcome, and let it go on."""
    try:
        yield
    except UsageError as error:
        record_outcome(cwd, command, f"refused: {error}", ok=False)
        raise
    except KeyboardInterrupt:
        record_outcome(cwd, command, "cancelled", ok=False)
        raise
