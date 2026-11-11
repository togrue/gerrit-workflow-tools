"""Rewriting the **local stack**: reorder, fold or drop its commits — or nothing at all.

All or nothing: a rewrite that would conflict is refused before anything is touched (see
:mod:`reorder_preview`), and one that conflicts anyway is aborted, so the branch is either
as asked or exactly as it was. Runs ``git rebase -i`` with this module as the sequence editor
(``python -m gerrit_workflow_tools.core.reorder``), which only writes the prepared todo.

Folding never merges two commit messages: a message with two **Change-Id** footers is not a
Gerrit change. One of the two messages survives whole, and with it its Change-Id.
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.git_editor import module_editor_command
from gerrit_workflow_tools.core.git_run import commit_sha_or_none, git, git_out
from gerrit_workflow_tools.core.git_state import checked_out_branch_name, rebase_in_progress
from gerrit_workflow_tools.core.reorder_preview import PickConflict, PreviewUnavailable, first_conflict
from gerrit_workflow_tools.core.stack import Commit, get_stack_snapshot


TODO_ENV = "GER_REORDER_TODO"

StepAction = Literal["pick", "fixup", "fixup-take-message", "drop"]
"""What happens to one commit: kept; folded into the commit below it, keeping that commit's
message (``fixup``) or its own (``fixup-take-message``); or removed."""

STEP_ACTIONS: tuple[StepAction, ...] = ("pick", "fixup", "fixup-take-message", "drop")

_TODO_WORD: dict[StepAction, str] = {
    "pick": "pick",
    "fixup": "fixup",
    "fixup-take-message": "fixup -C",
    "drop": "drop",
}


class ReorderRefusal(RuntimeError):
    """The stack was left as it was: the request, the worktree, or a conflict stands in the way."""


@dataclass(frozen=True)
class StackStep:
    """One line of a stack rewrite: a commit (full or abbreviated id) and what happens to it."""

    sha: str
    action: StepAction = "pick"


@dataclass(frozen=True)
class StackRewrite:
    """A finished rewrite: the new stack, and where the branch was, to go back to."""

    commits: list[Commit]
    previous_head: str


def _refuse_unless_ready(cwd: Path | str, upstream_tip: str) -> None:
    if rebase_in_progress(cwd):
        raise ReorderRefusal("a rebase is already in progress; finish or abort it first")
    if checked_out_branch_name(cwd) is None:
        raise ReorderRefusal("HEAD is detached; check out the branch whose stack you want to rewrite")
    if git_out("status", "--porcelain", "--untracked-files=no", cwd=cwd):
        raise ReorderRefusal("the worktree has uncommitted changes; commit or stash them first")
    if git_out("rev-list", "--merges", f"{upstream_tip}..HEAD", cwd=cwd):
        raise ReorderRefusal("the local stack contains a merge commit; rewrite it by hand with ger rebase")


def _resolve_steps(stack: Sequence[Commit], steps: Sequence[StackStep]) -> list[tuple[Commit, StepAction]]:
    """*steps* against the stack: each commit named exactly once, and nothing folded into thin air."""
    resolved: list[tuple[Commit, StepAction]] = []
    for step in steps:
        matches = [c for c in stack if step.sha and c.sha.startswith(step.sha.lower())]
        if len(matches) != 1:
            raise ReorderRefusal(f"{step.sha!r} does not name exactly one commit of the local stack")
        if step.action not in STEP_ACTIONS:
            raise ReorderRefusal(f"unknown action {step.action!r}; use one of {', '.join(STEP_ACTIONS)}")
        resolved.append((matches[0], step.action))
    if len(resolved) != len(stack) or {c.sha for c, _ in resolved} != {c.sha for c in stack}:
        raise ReorderRefusal(
            f"the rewrite must name each of the {len(stack)} local stack commits once "
            "(the stack may have changed: reload it)"
        )
    kept = [(commit, action) for commit, action in resolved if action != "drop"]
    if kept and kept[0][1] != "pick":
        raise ReorderRefusal(f'"{kept[0][0].subject}" is the lowest commit: there is nothing below to fold it into')
    return resolved


def _does_not_apply(commit: Commit | None, files: Sequence[str] = ()) -> ReorderRefusal:
    where = f'"{commit.subject}" ({commit.short_sha})' if commit else "a commit"
    conflict = f" (conflict in {', '.join(files)})" if files else ""
    return ReorderRefusal(f"{where} does not apply in its new place{conflict}; the stack was left as it was")


def _git_reason(output: str | None) -> str:
    """The line of git's output that says why: its first ``fatal:`` or ``error:``, else its last line."""
    lines = [line.strip() for line in (output or "").splitlines() if line.strip()]
    reason = next((line for line in lines if line.startswith(("fatal:", "error:"))), lines[-1] if lines else "")
    return reason or "no reason given"


def _preview(cwd: Path | str, base: str, picks: Sequence[Commit], stack: Sequence[Commit]) -> PickConflict | None:
    try:
        return first_conflict(cwd, base, picks, stack)
    except PreviewUnavailable:
        return None  # an older git: the rebase below finds out, and is aborted


def rewrite_stack(cwd: Path | str, steps: Sequence[StackStep], *, settings: Settings) -> StackRewrite:
    """Rewrite the local stack of the checked-out branch as *steps* say (oldest first).

    Raises :class:`ReorderRefusal`, with the branch untouched, when *steps* do not cover the
    stack, the worktree is not clean, or a commit does not apply in its new place.
    """
    snapshot = get_stack_snapshot(cwd, first_parent=True, settings=settings)
    head = commit_sha_or_none(cwd, "HEAD") or ""
    resolved = _resolve_steps(snapshot.commits, steps)
    if [(c.sha, action) for c, action in resolved] == [(c.sha, "pick") for c in snapshot.commits]:
        return StackRewrite(list(snapshot.commits), head)
    _refuse_unless_ready(cwd, snapshot.upstream_tip)
    kept = [commit for commit, action in resolved if action != "drop"]
    if conflict := _preview(cwd, snapshot.upstream_tip, kept, snapshot.commits):
        raise _does_not_apply(conflict.commit, conflict.files)

    with tempfile.TemporaryDirectory(prefix="ger-reorder-") as tmp:
        todo = Path(tmp) / "todo"
        todo.write_text("".join(f"{_TODO_WORD[action]} {c.sha}\n" for c, action in resolved), encoding="utf-8")
        env = {
            "GIT_SEQUENCE_EDITOR": module_editor_command("gerrit_workflow_tools.core.reorder"),
            "GIT_EDITOR": "true",
            TODO_ENV: str(todo),
        }
        done = git("rebase", "-i", "--no-autosquash", snapshot.upstream_tip, cwd=cwd, env=env, check=False)
    if done.returncode != 0:
        stopped_at = git("rev-parse", "-q", "--verify", "REBASE_HEAD", cwd=cwd, check=False).stdout.strip()
        conflicted = git("diff", "--name-only", "--diff-filter=U", cwd=cwd, check=False).stdout.split()
        git("rebase", "--abort", cwd=cwd, check=False)
        if not conflicted:
            # Not a conflict: git refused for a reason of its own (a hook, no committer identity).
            raise ReorderRefusal(
                f"git rebase failed: {_git_reason(done.stderr or done.stdout)}; the stack was left as it was"
            )
        raise _does_not_apply(next((c for c, _ in resolved if c.sha == stopped_at), None), conflicted)
    return StackRewrite(list(get_stack_snapshot(cwd, first_parent=True, settings=settings).commits), head)


def reorder_stack(cwd: Path | str, order: Sequence[str], *, settings: Settings) -> list[Commit]:
    """Put the local stack into *order* (oldest first), every commit kept. See :func:`rewrite_stack`."""
    return rewrite_stack(cwd, [StackStep(sha) for sha in order], settings=settings).commits


def undo_rewrite(cwd: Path | str, rewrite_head: str, previous_head: str) -> None:
    """Put the branch back on *previous_head*, if it still is where the rewrite left it.

    Refuses once anything was committed on top, and keeps uncommitted edits (``reset --keep``).
    """
    if commit_sha_or_none(cwd, "HEAD") != commit_sha_or_none(cwd, rewrite_head):
        raise ReorderRefusal("the branch has moved since the rewrite; undo it by hand from the reflog")
    if commit_sha_or_none(cwd, previous_head) is None:
        raise ReorderRefusal(f"{previous_head[:12]} is not a commit here")
    if rebase_in_progress(cwd) or checked_out_branch_name(cwd) is None:
        raise ReorderRefusal("a rebase is in progress or HEAD is detached; nothing was undone")
    back = git("reset", "--keep", "-q", previous_head, cwd=cwd, check=False)
    if back.returncode != 0:
        raise ReorderRefusal("your uncommitted changes are in the way of the undo; nothing was changed")


def main(argv: list[str] | None = None) -> int:
    """``GIT_SEQUENCE_EDITOR`` entry: replace git's todo with the prepared one."""
    args = sys.argv if argv is None else argv
    prepared = os.environ.get(TODO_ENV)
    if len(args) < 2 or not prepared:
        return 1
    Path(args[1]).write_text(Path(prepared).read_text(encoding="utf-8"), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
