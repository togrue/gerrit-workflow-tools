"""Why rewriting the local stack, or a switch, cannot start over what the index and work tree hold.

Every history rewrite asks :func:`history_rewrite_refusal` before it starts; ``ger review`` and
the reorder ask the parts they need. A rebase underway is :mod:`gerrit_workflow_tools.core.rebase_state`.
"""

from __future__ import annotations

import shlex
from pathlib import Path

from gerrit_workflow_tools.core.rebase_state import rebase_refusal
from gerrit_workflow_tools.core.tracked_changes import TrackedChanges, tracked_changes


def _then(rerun: str | None) -> str:
    return f", then run {rerun} again" if rerun else " first"


def _quoted(paths: tuple[str, ...]) -> str:
    return " ".join(shlex.quote(path) for path in paths)


def submodule_refusal(
    cwd: Path | str | None, base: str = "HEAD", *, rerun: str | None = None, changes: TrackedChanges | None = None
) -> str | None:
    """Why ger cannot go on over a submodule change ``git stash`` does not save, or ``None``.

    The one wording for both: a submodule a file or symlink replaced in the work tree (a switch
    or rebase would delete that file), and a submodule staged at another commit than *base*
    records. *rerun* names the command to run again; without it, the refusal is a history
    rewrite's. *changes* is :func:`tracked_changes` against *base*, when the caller has it.

    The interactive rebase does not check for a staged submodule before it starts (its own check
    leaves submodules out), so it would start and then stop at its first commit with the rebase
    underway ("your local changes would be overwritten"); an ``edit`` stop it did reach would take
    the staged gitlink into the commit being amended. Neither is saved by ``git stash`` alone, so
    neither can pass as uncommitted changes to stash.

    When *base* is not ``HEAD`` (a staged ``ger review``: the index holds *base*, ``HEAD`` its
    parent), committing would commit the review too, so no commit is advised.
    """
    changes = changes if changes is not None else tracked_changes(cwd, base)
    if paths := changes.replaced_submodules:
        commit = "" if base != "HEAD" else f"commit it (`git add {_quoted(paths)}`, then `git commit`) or "
        return (
            f"a file or symlink replaces the submodule at {', '.join(paths)}; `git stash` does not save it "
            f"and a switch or rebase would delete it: {commit}move it out of the way{_then(rerun)}"
        )
    if not (paths := changes.staged_submodules):
        return None
    source = "" if base == "HEAD" else f" --source={base[:12]}"
    unstage = f"git restore --staged{source} {_quoted(paths)}"
    staged = f"the submodule commit staged for {', '.join(paths)}"
    if base != "HEAD":
        return f"{staged} is not the one {base[:12]} records; `{unstage}`{_then(rerun)}"
    why = "" if rerun else " (a rebase would stop on it, or fold it into the commit it stops at)"
    return f"{staged} is not committed; commit it or `{unstage}`{_then(rerun)}{why}"


def uncommitted_changes_refusal(cwd: Path | str | None, changes: TrackedChanges | None = None) -> str | None:
    """Why rewriting cannot start over uncommitted changes to tracked files, or ``None``.

    What counts is what ``git stash`` would save (:attr:`TrackedChanges.files`): a submodule's
    own state is left out, as git's own check before a rebase leaves it out; a submodule change
    stash does not save is :func:`submodule_refusal`.
    """
    if (changes if changes is not None else tracked_changes(cwd)).files:
        return (
            "you have uncommitted changes; commit them (`ger fix REV` folds them into a stack commit) "
            "or `git stash` them first"
        )
    return None


def history_rewrite_refusal(cwd: Path | str | None) -> str | None:
    """Why rewriting the local stack cannot start here, or ``None``: the one wording every rewrite shares.

    Git refuses an interactive rebase over a rebase underway or uncommitted changes to tracked
    files; asking first replaces git's bare refusal with what to do about it. A submodule change
    stash does not save is refused too: a staged one before git starts a rebase that would stop
    on it, a file replacing one before the rebase deletes that file.
    """
    if refusal := rebase_refusal(cwd):
        return refusal
    changes = tracked_changes(cwd)
    return uncommitted_changes_refusal(cwd, changes) or submodule_refusal(cwd, changes=changes)
