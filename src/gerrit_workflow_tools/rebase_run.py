"""Running ``git rebase`` from a command: the refusal before it starts, the next step at a stop.

Shared by ``ger edit`` / ``ger reword`` and ``ger rebase``, so every ger-started rebase refuses
and stops the same way. The rebase talks to the terminal itself, without git's own advice
(:func:`_git_rebase`); this module adds ger's own lines after it: where it stopped and the one
``next:`` step from :func:`~gerrit_workflow_tools.core.rebase_next_step.stop_next_step`.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Literal

from gerrit_workflow_tools.cli_terminal import stdin_is_terminal
from gerrit_workflow_tools.core.git_editor import module_editor_command
from gerrit_workflow_tools.core.git_run import git, git_out
from gerrit_workflow_tools.core.rebase_next_step import ABORT, go_on_refusal, stop_next_step, where_head_is
from gerrit_workflow_tools.core.rebase_state import RebaseAction, RebaseStop
from gerrit_workflow_tools.core.rebasing_stack import stopped_rebase
from gerrit_workflow_tools.core.rewrite_outcome import (
    DroppedCommit,
    RewriteOutcome,
    dropped_commit,
    rewrite_outcome,
    stack_before,
)
from gerrit_workflow_tools.core.rewrite_refusal import history_rewrite_refusal
from gerrit_workflow_tools.core.run_context import RunContext
from gerrit_workflow_tools.core.stack import UNSQUASHED_FIXUP_ADVICE, read_commit_rows
from gerrit_workflow_tools.core.usage import UsageError
from gerrit_workflow_tools.core.words import counted
from gerrit_workflow_tools.render.wrap import say


def require_history_rewrite(cwd: Path) -> None:
    """Refuse to start rewriting the local stack where git would refuse: one message per reason."""
    if refusal := history_rewrite_refusal(cwd):
        raise UsageError(refusal)


_ADVICE_OFF = tuple(
    arg
    for key in ("mergeConflict", "resolveConflict", "waitingForEditor", "skippedCherryPicks")
    for arg in ("-c", f"advice.{key}=false")
)
"""git's advice, off for the one invocation (never written to the user's config). A git that does
not know a key ignores it."""

# What git says on stderr that ger says better after it: hints (an old git's conflict advice has no
# key), progress, where it stopped, and the success line `rebase finished: …` replaces. What the
# merge says of conflicts goes to stdout, untouched.
_GITS_OWN = re.compile(
    r"(hint: |Rebasing \(\d+/\d+\)$|Stopped at [0-9a-f]+\.\.\.|(error: c|C)ould not apply [0-9a-f]+\.\.\."
    r"|Successfully rebased and updated )"
)
# git's instructions no advice key turns off: from the first line through `  git rebase --continue`.
_GITS_INSTRUCTIONS = (
    "You can amend the commit now, with",
    "You can fix the problem, and then run",
    "Commit or stash your changes, and then run",
)
_ERASE = re.compile(r"\x1b\[K")


class _GitsWords:
    """The lines of git's stderr worth keeping beside ger's (conflicts, errors, warnings), each on its
    own line; git's advice, progress and the next step in its words dropped. Text git localises
    passes through as it is."""

    def __init__(self) -> None:
        self._in_instructions = False

    def __call__(self, line: str) -> None:
        text = _ERASE.sub("", line).rstrip()
        if self._in_instructions:
            self._in_instructions = text.strip() != "git rebase --continue"
            return
        if text in _GITS_INSTRUCTIONS:
            self._in_instructions = True
            return
        if text.strip() and not _GITS_OWN.match(text):
            print(text, file=sys.stderr, flush=True)


def _git_rebase(cwd: Path, *args: str, env: dict[str, str] | None) -> int:
    """``git rebase *args*`` talking to the terminal, with git's advice off: its exit code.

    The one way every ger command runs a rebase the user sees, so only ger's ``stopped at …`` and
    ``next:`` lines say what to do.
    """
    return git(
        *_ADVICE_OFF, "rebase", *args, cwd=cwd, env=env, check=False, capture=False, on_stderr=_GitsWords()
    ).returncode


def run_interactive_rebase(
    run: RunContext,
    base: str,
    *,
    sequence_editor: str,
    env: dict[str, str],
    walk_on: str | None = None,
    autosquash: bool | None = False,
    undo: Undo = "cherry-pick",
) -> int:
    """``git rebase -i`` with package module *sequence_editor* as editor; return git's exit code.

    *autosquash* ``False`` keeps every ``fixup!`` commit where it is whatever ``rebase.autosquash``
    says, ``True`` folds them, ``None`` leaves it to git config. A stop ends with the shared next
    step, naming *walk_on* as the way on to the next attention stop; a finished rebase with what it
    did (:func:`print_outcome`, getting a dropped commit back by *undo*).
    """
    before = stack_before(run)
    env = {**env, "GIT_SEQUENCE_EDITOR": module_editor_command(f"gerrit_workflow_tools.{sequence_editor}")}
    squash = [] if autosquash is None else ["--autosquash" if autosquash else "--no-autosquash"]
    code = _git_rebase(run.cwd, "-i", *squash, base, env=env)
    if not print_stop(run.cwd, walk_on) and code == 0:
        previous = before[-1].short_sha if before else None  # HEAD before the rebase: the stack ends there
        print_outcome(rewrite_outcome(run, before), walk_on=walk_on, undo=undo, previous_head=previous)
    return code


def go_on_with_rebase(run: RunContext, action: RebaseAction, *, walk_on: str | None = None) -> int:
    """``git rebase --<action>`` of the rebase underway; return git's exit code. Output goes to the terminal.

    Refused first (:class:`UsageError`) where :func:`go_on_refusal` says git must not run: ``ger
    rebase --continue|--skip`` and ``ger edit -n`` go on the same way. Without a terminal nobody can
    edit a message (a resolved conflict's commit, a later ``reword`` or ``squash``), so it is taken as
    it is, unless the caller set ``GIT_EDITOR``. A skip says which commit it dropped and how to get it
    back; the next stop ends with the shared next step, naming *walk_on* as the way on to the next
    attention stop; a finished rebase says what it did against the branch before it (``orig-head``),
    and an abort where the branch is back at.
    """
    cwd = run.cwd
    progress = stopped_rebase(cwd)
    if refusal := go_on_refusal(cwd, action, progress, walk_on=walk_on):
        raise UsageError(refusal)
    assert progress is not None  # go_on_refusal refuses without one
    before = stack_before(run, progress.orig_head) if progress.orig_head else None
    env = None if stdin_is_terminal() or "GIT_EDITOR" in os.environ else {"GIT_EDITOR": "true"}
    code = _git_rebase(cwd, f"--{action.value}", env=env)
    if action is RebaseAction.ABORT:
        if code == 0:
            print(f"rebase aborted: {where_head_is(cwd)}, as before the rebase", file=sys.stderr)
        return code
    now = stopped_rebase(cwd)
    if now is not None:
        skipped = action is RebaseAction.SKIP and progress.current and now.current != progress.current
        if skipped and progress.current and not progress.committed_by_hand:
            print_dropped(dropped_commit(run, progress.current), undo="cherry-pick", still_underway=True)
        print_stop(cwd, walk_on)
    elif code == 0 and before is not None:
        print_outcome(rewrite_outcome(run, before), walk_on=walk_on, undo="cherry-pick")
    return code


def print_stop(cwd: Path, walk_on: str | None) -> bool:
    """Where the rebase underway stopped, and the next step; ``False``, and nothing said, when none is underway."""
    if (progress := stopped_rebase(cwd)) is None:
        return False
    if progress.current:
        conflict = " (conflict)" if progress.stop is RebaseStop.CONFLICT else ""
        conflict = " (conflict, resolved by git rerere)" if progress.reused and not progress.unmerged else conflict
        if progress.folding_into and (target := read_commit_rows(cwd, "--no-walk", progress.folding_into)):
            fixup = git_out("log", "-1", "--format=%h", progress.current, cwd=cwd)
            say(f"stopped while folding fixup {fixup} into {target[0].short_sha} {target[0].subject}{conflict}")
        else:
            say(f"stopped at {git_out('log', '-1', '--format=%h %s', progress.current, cwd=cwd)}{conflict}")
    say(f"next: {stop_next_step(progress, walk_on=walk_on)}")
    return True


Undo = Literal["cherry-pick", "reset"]
"""How a dropped commit comes back: picked onto the branch, or the whole rewrite reset away (only a drop)."""


def print_dropped(
    dropped: DroppedCommit, *, undo: Undo, previous_head: str | None = None, still_underway: bool = False
) -> None:
    """The two lines every ger command says of a commit it dropped: which one, and how to get it back.

    No question asked: the drop is said and can be taken back. *undo* ``reset`` (``ger edit --drop``,
    which changed nothing else) resets to *previous_head*; ``cherry-pick`` picks the commit back on top,
    once the rebase finished when it is *still_underway*.
    """
    change = f"; its change {dropped.change_number} stays open on Gerrit" if dropped.change_number else ""
    say(f"dropped {dropped.short_sha} {dropped.subject}{change}")
    if undo == "reset" and previous_head:
        back = f"git reset --keep {previous_head}"
    elif still_underway:
        back = f"once the rebase is finished, git cherry-pick {dropped.short_sha} (or {ABORT} now)"
    else:
        back = f"git cherry-pick {dropped.short_sha}"
    say(f"to get it back: {back}")


def print_outcome(
    outcome: RewriteOutcome,
    *,
    walk_on: str | None,
    undo: Undo,
    previous_head: str | None = None,
    done: str = "rebase finished",
) -> None:
    """ger's closing lines after a rewrite finished: what it dropped, what it did in words, and the next step.

    No ``next:`` during an attention walk (*walk_on*): the walk goes on to its next stop.
    """
    for dropped in outcome.dropped:
        print_dropped(dropped, undo=undo, previous_head=previous_head)
    say(f"{done}: {outcome_text(outcome)}")
    if walk_on:
        return
    if outcome.fixups_left:
        say(f"next: {UNSQUASHED_FIXUP_ADVICE}")
    elif outcome.rewritten or outcome.dropped:
        say("next: `ger push` uploads the stack as it is now")


def outcome_text(outcome: RewriteOutcome) -> str:
    """What a finished rewrite did, in words: ``rewrote all 3 commits, now ahead of Gerrit; squashed 1 fixup``."""
    rewritten, total = outcome.rewritten, outcome.commits
    if not (rewritten or outcome.dropped or outcome.squashed or outcome.upstream):
        return f"nothing changed, the local stack is as it was ({counted(total, 'commit')})"
    said = [f"dropped {counted(len(outcome.dropped), 'commit')}"] if outcome.dropped else []
    if rewritten:
        whole = "all " if rewritten == total > 1 else ""
        part = f" of {total}" if rewritten < total else ""
        said.append(
            f"rewrote {whole}{rewritten}{part} commit{'' if rewritten == total == 1 else 's'}{_on_gerrit(outcome)}"
        )
    elif total:
        said.append(
            f"the other {counted(total, 'commit')} as they were"
            if outcome.dropped
            else f"{counted(total, 'commit')} as they were"
        )
    if outcome.squashed:
        said.append(f"squashed {counted(outcome.squashed, 'fixup')}")
    if outcome.upstream:
        said.append(f"left out {counted(outcome.upstream, 'commit')} already upstream")
    return "; ".join(said)


def _on_gerrit(outcome: RewriteOutcome) -> str:
    """Where the rewritten commits stand against Gerrit, as the cache knows it; nothing without a Gerrit."""
    ahead, rewritten = outcome.on_gerrit, outcome.rewritten
    if ahead is None:
        return ""
    if ahead == rewritten:
        return ", now ahead of Gerrit" if rewritten == 1 else ", all now ahead of Gerrit"
    if ahead == 0:
        return ", none on Gerrit yet"
    return f": {ahead} now ahead of Gerrit, {rewritten - ahead} not on Gerrit yet"
