"""Rewording a commit from a command: the message from an editor, and what to say once it is done.

``ger reword`` and ``ger edit --reword`` reword without a rebase
(:func:`~gerrit_workflow_tools.core.reword.reword_commit`): only messages change, so uncommitted
changes stay and nothing can conflict. A message left as it was changes nothing at all. Done, it
says so in the words a finished rebase uses (:func:`~gerrit_workflow_tools.rebase_run.print_outcome`).
"""

from __future__ import annotations

import sys

from gerrit_workflow_tools.core.git_editor import run_git_editor
from gerrit_workflow_tools.core.git_run import GitError, git_out
from gerrit_workflow_tools.core.reword import message_from_edit, message_to_edit, reword_commit
from gerrit_workflow_tools.core.rewrite_outcome import rewrite_outcome, stack_before
from gerrit_workflow_tools.core.run_context import RunContext
from gerrit_workflow_tools.core.usage import Refusal
from gerrit_workflow_tools.rebase_run import print_outcome


def message_from_editor(run: RunContext, sha: str) -> str:
    """The message the user's editor (``GIT_EDITOR``, ``core.editor``, ``VISUAL``, ``EDITOR``) leaves for *sha*."""
    if (repo_dir := run.worktree.repository_dir) is None:
        raise GitError("not a git repository; run ger inside a clone")
    path = repo_dir / "ger" / "REWORD_EDITMSG"
    path.parent.mkdir(parents=True, exist_ok=True)
    commit = git_out("log", "-1", "--format=%h %s", sha, cwd=run.cwd)
    path.write_text(message_to_edit(git_out("log", "-1", "--format=%B", sha, cwd=run.cwd), commit), encoding="utf-8")
    if (code := run_git_editor(path, run.cwd, settings=run.settings)) != 0:
        raise Refusal(f"the editor exited with {code}; {commit} was left as it was")
    return message_from_edit(path.read_text(encoding="utf-8"))


def reword(run: RunContext, sha: str, message: str) -> int:
    """Give *sha* the commit message *message* without a rebase; say what changed and how to take it back."""
    before = stack_before(run)
    done = reword_commit(run.cwd, sha, message, settings=run.settings)
    at = next(i for i, c in enumerate(before) if c.sha == sha)
    old, now = before[at], done.commits[at]  # a reword keeps every commit where it was
    if now.sha == old.sha:
        print(f"the message of {old.short_sha} {old.subject} is unchanged: the stack is as it was", file=sys.stderr)
        return 0
    print(f"reworded {old.short_sha} → {now.short_sha} {now.subject}", file=sys.stderr)
    print(f"to take it back: git reset --keep {before[-1].short_sha}", file=sys.stderr)
    ids = {c.valid_change_id for c in done.commits}
    for lost in sorted(c.valid_change_id for c in before if c.valid_change_id and c.valid_change_id not in ids):
        print(
            f"warning: Change-Id {lost} left the stack; the next push creates a new change. "
            f"The old change stays open on Gerrit: put `Change-Id: {lost}` back with `ger reword {now.short_sha}` "
            "to keep updating it, or abandon it there.",
            file=sys.stderr,
        )
    print_outcome(rewrite_outcome(run, before), walk_on=None, undo="reset", done="reword finished")
    return 0
