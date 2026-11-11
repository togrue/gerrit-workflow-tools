"""A rebase underway and what it means for the next command: the refusals and the next step.

Every history-moving ger command asks here before it starts, and every ger-started rebase that
stops on a conflict ends with :func:`conflict_next_step`, so each reason has one wording.
Which branch a stopped rebase belongs to stays with branch resolution in
:mod:`gerrit_workflow_tools.core.git_state`.
"""

from __future__ import annotations

from pathlib import Path

from gerrit_workflow_tools.core.git_run import git, git_out
from gerrit_workflow_tools.core.git_state import git_dir


def rebase_in_progress(cwd: Path | str | None) -> bool:
    """True while git has a rebase underway, including one stopped at a commit."""
    repo_dir = git_dir(cwd)
    return repo_dir is not None and any((repo_dir / d).exists() for d in ("rebase-merge", "rebase-apply"))


def rebase_refusal(cwd: Path | str | None) -> str | None:
    """Why a command that moves branches cannot start during a rebase, or ``None``."""
    if rebase_in_progress(cwd):
        return "a rebase is already in progress; run `git rebase --continue` or `git rebase --abort` first"
    return None


def unmerged_paths(cwd: Path | str | None) -> list[str]:
    """Paths the index holds as unresolved conflicts (``git diff --diff-filter=U``)."""
    out = git("diff", "--name-only", "--diff-filter=U", cwd=cwd, check=False).stdout or ""
    return [line for line in out.splitlines() if line.strip()]


def conflict_next_step(cwd: Path | str | None, *, continue_with: str = "git rebase --continue") -> str | None:
    """The one next step after a rebase stopped on a conflict, or ``None`` when none is stopped on one.

    Every ger command that starts or continues a rebase prints this when it stops on a conflict,
    and every command that must not run on a conflicted index refuses with it.
    """
    paths = unmerged_paths(cwd)
    if not paths:
        return None
    if not rebase_in_progress(cwd):
        return f"resolve the conflicts in {', '.join(paths)} and `git add` them, then finish the merge or pick"
    return (
        f"resolve the conflicts in {', '.join(paths)}, `git add` them, then `{continue_with}`; "
        "`git rebase --abort` goes back to where the rebase started"
    )


def history_rewrite_refusal(cwd: Path | str | None) -> str | None:
    """Why rewriting the local stack cannot start here, or ``None``: the one wording every rewrite shares.

    Git refuses an interactive rebase over a rebase underway or uncommitted changes to tracked
    files; asking first replaces git's bare refusal with what to do about it.
    """
    if refusal := rebase_refusal(cwd):
        return refusal
    if git_out("status", "--porcelain", "--untracked-files=no", cwd=cwd):
        return (
            "you have uncommitted changes; commit them (`ger fix REV` folds them into a stack commit) "
            "or `git stash` them first"
        )
    return None
