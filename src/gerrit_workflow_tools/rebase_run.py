"""Running ``git rebase`` from a command: the refusal before it starts, the next step at a stop.

Shared by ``ger edit`` / ``ger reword`` and ``ger rebase``, so every ger-started rebase refuses
and stops the same way. The rebase talks to the terminal itself; this module only adds ger's own
lines after it: where it stopped and the one ``next:`` step from
:func:`~gerrit_workflow_tools.core.rebase_next_step.stop_next_step`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from gerrit_workflow_tools.cli_terminal import stdin_is_terminal
from gerrit_workflow_tools.core.git_editor import module_editor_command
from gerrit_workflow_tools.core.git_run import git, git_out
from gerrit_workflow_tools.core.rebase_next_step import go_on_refusal, stop_next_step, where_head_is
from gerrit_workflow_tools.core.rebase_state import RebaseAction, RebaseStop
from gerrit_workflow_tools.core.rebasing_stack import stopped_rebase
from gerrit_workflow_tools.core.rewrite_refusal import history_rewrite_refusal
from gerrit_workflow_tools.core.usage import UsageError


def require_history_rewrite(cwd: Path) -> None:
    """Refuse to start rewriting the local stack where git would refuse: one message per reason."""
    if refusal := history_rewrite_refusal(cwd):
        raise UsageError(refusal)


def run_interactive_rebase(
    cwd: Path,
    base: str,
    *,
    sequence_editor: str,
    env: dict[str, str],
    walk_on: str | None = None,
    autosquash: bool | None = False,
) -> int:
    """``git rebase -i`` with package module *sequence_editor* as editor; return git's exit code.

    *autosquash* ``False`` keeps every ``fixup!`` commit where it is whatever ``rebase.autosquash``
    says, ``True`` folds them, ``None`` leaves it to git config. A stop ends with the shared next
    step, naming *walk_on* as the way on to the next attention stop.
    """
    env = {**env, "GIT_SEQUENCE_EDITOR": module_editor_command(f"gerrit_workflow_tools.{sequence_editor}")}
    squash = [] if autosquash is None else ["--autosquash" if autosquash else "--no-autosquash"]
    code = git("rebase", "-i", *squash, base, cwd=cwd, env=env, check=False, capture=False).returncode
    print_stop(cwd, walk_on)
    return code


def go_on_with_rebase(cwd: Path, action: RebaseAction, *, walk_on: str | None = None) -> int:
    """``git rebase --<action>`` of the rebase underway; return git's exit code. Output goes to the terminal.

    Refused first (:class:`UsageError`) where :func:`go_on_refusal` says git must not run: ``ger
    rebase --continue|--skip`` and ``ger edit -n`` go on the same way. Without a terminal nobody can
    edit a message (a resolved conflict's commit, a later ``reword`` or ``squash``), so it is taken as
    it is, unless the caller set ``GIT_EDITOR``. The next stop ends with the shared next step, naming
    *walk_on* as the way on to the next attention stop; an abort says where the branch is back at.
    """
    if refusal := go_on_refusal(cwd, action, stopped_rebase(cwd), walk_on=walk_on):
        raise UsageError(refusal)
    env = None if stdin_is_terminal() or "GIT_EDITOR" in os.environ else {"GIT_EDITOR": "true"}
    code = git("rebase", f"--{action.value}", cwd=cwd, env=env, check=False, capture=False).returncode
    if action is not RebaseAction.ABORT:
        print_stop(cwd, walk_on)
    elif code == 0:
        print(f"rebase aborted: {where_head_is(cwd)}, as before the rebase", file=sys.stderr)
    return code


def print_stop(cwd: Path, walk_on: str | None) -> None:
    """Where the rebase underway stopped, and the next step; nothing when none is underway."""
    if (progress := stopped_rebase(cwd)) is None:
        return
    if progress.current and progress.stop is not RebaseStop.CONFLICT:  # git named a conflict's commit itself
        print(f"stopped at {git_out('log', '-1', '--format=%h %s', progress.current, cwd=cwd)}", file=sys.stderr)
    print(f"next: {stop_next_step(progress, walk_on=walk_on)}", file=sys.stderr)
