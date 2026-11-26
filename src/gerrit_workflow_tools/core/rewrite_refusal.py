"""Why rewriting the local stack, or a switch, cannot start: over what the index and work tree hold,
because Gerrit already closed the change a rewrite targets, or because the rewrite would have to
replay a merge commit.

Every history rewrite asks :func:`history_rewrite_refusal` before it starts; ``ger review`` and
the reorder ask the parts they need. A rebase underway is :mod:`gerrit_workflow_tools.core.rebase_state`.
``ger edit``, ``ger reword`` and ``ger fix`` also ask :func:`closed_change_refusal` and
:func:`pinned_target_refusal` of their target; every rebase ger starts asks :func:`merge_replay_refusal`.
"""

from __future__ import annotations

import logging
import shlex
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from gerrit_workflow_tools.core.config import ConfigError
from gerrit_workflow_tools.core.gerrit.change_view import ChangeView
from gerrit_workflow_tools.core.gerrit.rest import GerritApiError
from gerrit_workflow_tools.core.gerrit.stack_context import ChangeResolutionError
from gerrit_workflow_tools.core.git_run import git
from gerrit_workflow_tools.core.rebase_state import rebase_refusal
from gerrit_workflow_tools.core.stack import StackSnapshot, rewrite_base
from gerrit_workflow_tools.core.tracked_changes import TrackedChanges, tracked_changes


if TYPE_CHECKING:
    from gerrit_workflow_tools.core.gerrit.service import GerritService


logger = logging.getLogger(__name__)


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


_COMMAND = {"drop": "edit --drop"}
_RERERE = "with `git config rerere.enabled true` first, or a redone merge asks for its conflicts again"


def merge_replay_refusal(cwd: Path | str | None, base: str) -> str | None:
    """Why ``git rebase -i <base>`` must not start: a merge commit in ``base..HEAD``, or ``None``.

    Without ``--rebase-merges`` git leaves every merge out and replays the commits each one
    brought in onto the first-parent line: the history comes out flattened, a merge's Change-Id
    and whatever only the merge records are gone, and the rebase still succeeds. ger does not
    replay merges, so it refuses and names the git command that does. The newest merge is named.
    """
    listed = git("log", "-1", "--min-parents=2", "--format=%h %s", f"{base}..HEAD", cwd=cwd)
    if not (newest := listed.stdout.strip()):
        return None
    short, _, subject = newest.partition(" ")
    return (
        f'"{subject}" ({short}) is a merge commit, which git rebase would leave out, flattening the history; '
        f"nothing was changed. Rebase it yourself with `git rebase -i --rebase-merges {base[:12]}` ({_RERERE})"
    )


def pinned_target_refusal(
    cwd: Path | str | None, snapshot: StackSnapshot, pinned: int, target: str, *, action: str
) -> str | None:
    """Why stack commit *target* may not be rewritten by *action*, because a merge pins it, or ``None``.

    The lowest *pinned* commits of the local stack *snapshot*
    (:func:`~gerrit_workflow_tools.core.stack.pinned_count`) end at its newest merge. Rewriting one of
    those means replaying that merge, which :func:`merge_replay_refusal` refuses; the commits above
    it rebase from the merge and stay rewritable. *action* is ``edit``, ``reword``, ``drop`` or
    ``fix``. Asks git nothing unless it refuses.
    """
    commits = snapshot.commits
    index = next((i for i, commit in enumerate(commits) if commit.sha == target), None)
    if not pinned or index is None or index >= pinned:
        return None
    merge, fork = commits[pinned - 1], rewrite_base(cwd, snapshot, 0)
    by_hand = f"`git rebase -i --rebase-merges{' --autosquash' if action == 'fix' else ''} {fork[:12]}`"
    where = (
        "is a merge commit"
        if index == pinned - 1
        else f'is below the merge commit "{merge.subject}" ({merge.short_sha})'
    )
    short = commits[index].short_sha
    if action == "fix":
        return (
            f"{short} {where}, so `ger rebase --autosquash` could never fold a fixup into it: "
            f"that rebase would have to replay the merge. Commit it with `git commit --fixup={short}` "
            f"and fold it with {by_hand} ({_RERERE})"
        )
    return (
        f"{short} {where}, and ger does not replay merges: a rebase from below it would "
        f"flatten the history; nothing was changed. Use {by_hand} ({_RERERE}); the commits above "
        f"{merge.short_sha} can still be rewritten with `ger {_COMMAND.get(action, action)}`"
    )


def _status_refusal(statuses: Iterable[str | None], *, commit: str) -> str | None:
    """Why stack commit *commit* may not be rewritten, given what Gerrit says of its change, or ``None``.

    *statuses* are the Gerrit ``status`` of each change carrying its Change-Id on the push branch.
    One still open (``NEW``, or no status) is enough to go on, and so is none at all (not pushed).
    A merged change is refused before an abandoned one: fetching is what to do about it.
    """
    closed = {(status or "").strip().upper() for status in statuses}
    if not closed or closed - {"MERGED", "ABANDONED"}:
        return None
    if "MERGED" in closed:
        return (
            f"{commit} is already merged on Gerrit, so a new patch set for it would go nowhere; "
            "`git fetch`, then `ger rebase` to take it off the local stack"
        )
    return f"{commit} is abandoned on Gerrit; restore it there first, or drop the commit (`ger edit --drop {commit}`)"


def closed_change_refusal(
    service: GerritService,
    change_id: str | None,
    *,
    commit: str,
    statuses: Iterable[str | None] | None = None,
) -> str | None:
    """Why stack commit *commit*, carrying *change_id*, may not be rewritten because Gerrit closed its change.

    Git alone cannot tell: a change submitted since the last ``git fetch`` is still on the local
    stack. So Gerrit is asked, with at most one request (:meth:`ChangeApi.on_push_branch`; none
    while the cache is inside its trust window). When it cannot be asked (no ``ger.webUrl``,
    no credentials, unreachable, no stack context), the git-only check stands: ``None``.

    *statuses* is what Gerrit already said of the change in this run (an attention walk overlays
    the stack): then nothing is asked. *commit* names the commit in the message.
    """
    if statuses is not None:
        return _status_refusal(statuses, commit=commit)
    if not change_id:
        return None
    if not service.configured or not service.credentials_configured:
        logger.debug("not asking Gerrit whether %s is closed: no Gerrit configured", change_id)
        return None
    try:
        rows = service.changes.on_push_branch(change_id)
    except (GerritApiError, ConfigError, ChangeResolutionError) as exc:
        logger.debug("not asking Gerrit whether %s is closed: %s", change_id, exc)
        return None
    return _status_refusal((ChangeView.from_payload(row).status for row in rows), commit=commit)
