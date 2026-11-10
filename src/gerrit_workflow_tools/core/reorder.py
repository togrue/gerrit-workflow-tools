"""Reordering the **local stack**: the same commits, in a new order, or nothing at all.

All or nothing: a reorder that conflicts is aborted, so the branch is either in the new
order or exactly as it was. Runs ``git rebase -i`` with this module as the sequence editor
(``python -m gerrit_workflow_tools.core.reorder``), which only writes the prepared todo.
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.git_editor import module_editor_command
from gerrit_workflow_tools.core.git_run import git, git_out
from gerrit_workflow_tools.core.git_state import checked_out_branch_name, rebase_in_progress
from gerrit_workflow_tools.core.stack import Commit, get_stack_snapshot


TODO_ENV = "GER_REORDER_TODO"


class ReorderRefusal(RuntimeError):
    """The stack was left as it was: the request, the worktree, or a conflict stands in the way."""


def _refuse_unless_ready(cwd: Path | str, upstream_tip: str) -> None:
    if rebase_in_progress(cwd):
        raise ReorderRefusal("a rebase is already in progress; finish or abort it first")
    if checked_out_branch_name(cwd) is None:
        raise ReorderRefusal("HEAD is detached; check out the branch whose stack you want to reorder")
    if git_out("status", "--porcelain", "--untracked-files=no", cwd=cwd):
        raise ReorderRefusal("the worktree has uncommitted changes; commit or stash them first")
    if git_out("rev-list", "--merges", f"{upstream_tip}..HEAD", cwd=cwd):
        raise ReorderRefusal("the local stack contains a merge commit; reorder it by hand with ger rebase")


def _full_order(stack: Sequence[Commit], order: Sequence[str]) -> list[Commit]:
    """*order* (full or abbreviated SHAs) as stack commits; it must name each exactly once."""
    picked: list[Commit] = []
    for name in order:
        matches = [c for c in stack if name and c.sha.startswith(name.lower())]
        if len(matches) != 1:
            raise ReorderRefusal(f"{name!r} does not name exactly one commit of the local stack")
        picked.append(matches[0])
    if len(picked) != len(stack) or {c.sha for c in picked} != {c.sha for c in stack}:
        raise ReorderRefusal(
            f"the new order must name each of the {len(stack)} local stack commits once "
            "(the stack may have changed: reload it)"
        )
    return picked


def reorder_stack(cwd: Path | str, order: Sequence[str], *, settings: Settings) -> list[Commit]:
    """Rewrite the local stack of the checked-out branch into *order* (oldest first).

    Returns the new stack. Raises :class:`ReorderRefusal`, with the branch untouched, when
    *order* is not a permutation of the stack, the worktree is not clean, or a commit does
    not apply in its new place.
    """
    snapshot = get_stack_snapshot(cwd, first_parent=True, settings=settings)
    picked = _full_order(snapshot.commits, order)
    if [c.sha for c in picked] == [c.sha for c in snapshot.commits]:
        return list(snapshot.commits)
    _refuse_unless_ready(cwd, snapshot.upstream_tip)

    with tempfile.TemporaryDirectory(prefix="ger-reorder-") as tmp:
        todo = Path(tmp) / "todo"
        todo.write_text("".join(f"pick {c.sha}\n" for c in picked), encoding="utf-8")
        env = {
            "GIT_SEQUENCE_EDITOR": module_editor_command("gerrit_workflow_tools.core.reorder"),
            "GIT_EDITOR": "true",
            TODO_ENV: str(todo),
        }
        done = git("rebase", "-i", "--no-autosquash", snapshot.upstream_tip, cwd=cwd, env=env, check=False)
    if done.returncode != 0:
        stopped_at = git("rev-parse", "-q", "--verify", "REBASE_HEAD", cwd=cwd, check=False).stdout.strip()
        git("rebase", "--abort", cwd=cwd, check=False)
        culprit = next((c for c in picked if c.sha == stopped_at), None)
        where = f'"{culprit.subject}" ({culprit.short_sha})' if culprit else "a commit"
        raise ReorderRefusal(f"{where} does not apply in its new place; the stack was left as it was")
    return list(get_stack_snapshot(cwd, first_parent=True, settings=settings).commits)


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
