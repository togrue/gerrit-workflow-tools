"""The review worktree's folder: where it is, and what to do when something else is there.

One reusable worktree per repository (``ger.reviewWorktree``, default ``<repository>-review`` beside
the main worktree). The folder can outlive its registration: the repository was cloned again, or
``git worktree prune`` ran while the folder was out of reach. Such a left-over is moved aside, never
deleted (it may hold edits), and the worktree is made anew. Anything else there is not ger's to move.
"""

from __future__ import annotations

from pathlib import Path

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.git_run import GitError, git, git_out
from gerrit_workflow_tools.core.git_state import git_dir, repo_toplevel
from gerrit_workflow_tools.core.usage import UsageError


class ReviewWorktreeBlocked(UsageError):
    """The review worktree's folder holds something that is not this repository's worktree (exit 2)."""

    reason = "review-worktree-blocked"
    """What ``ger serve`` names it (``error.reason``), so a client can offer the review in this checkout."""


def main_worktree(cwd: Path | str) -> Path:
    """The repository's main worktree (the first ``git worktree list`` entry)."""
    for line in git_out("worktree", "list", "--porcelain", cwd=cwd).splitlines():
        if line.startswith("worktree "):
            return Path(line.removeprefix("worktree "))
    top = repo_toplevel(cwd)
    if top is None:
        raise GitError("not inside a git worktree")
    return top


def review_worktree_path(cwd: Path | str, settings: Settings) -> Path:
    """``ger.reviewWorktree`` (relative to the main worktree's parent), else ``<repo>-review`` beside it."""
    main = main_worktree(cwd)
    configured = (settings.review_worktree or "").strip()
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_absolute() else (main.parent / path)
    return main.parent / f"{main.name}-review"


def ensure_review_worktree(cwd: Path | str, path: Path, commit: str) -> Path | None:
    """Create the review worktree at *path* (detached at *commit*) unless it is already there.

    Returns where a left-over folder was moved to, when one was in the way. Raises
    :class:`ReviewWorktreeBlocked` for a folder that is not ger's to move.
    """
    set_aside: Path | None = None
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        found = _found_at(cwd, path)
        if found == "ours":
            return None
        if found != "left-over":
            raise ReviewWorktreeBlocked(
                f"the review worktree cannot be made at {path}: {found}. Nothing was changed. "
                "Review in this checkout instead (`ger review --in-place`), or name another folder: "
                "`git config ger.reviewWorktree <path>`"
            )
        set_aside = _move_aside(path)
    git("worktree", "add", "--detach", str(path), commit, cwd=cwd)
    return set_aside


def _found_at(cwd: Path | str, path: Path) -> str:
    """What the folder *path* is: ``ours``, ``left-over``, or words for what else it holds.

    Read from the files git keeps (a worktree's ``.git`` file names its entry in the repository),
    so no process is started.
    """
    if not path.is_dir():
        return "a file is there"
    dot_git = path / ".git"
    if dot_git.is_dir():
        return "another repository is there"
    entry = _worktree_entry(path, dot_git)
    if entry is None:
        return "the folder holds other files"
    if not entry.is_dir():
        return "left-over"
    here = git_dir(cwd)
    if here is not None and _common_dir(entry) == _common_dir(here):
        return "ours"
    return "a worktree of another repository is there"


def _worktree_entry(path: Path, dot_git: Path) -> Path | None:
    """The git dir a worktree's ``.git`` file names, whether or not it still exists."""
    try:
        text = dot_git.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text.startswith("gitdir:"):
        return None
    entry = Path(text.removeprefix("gitdir:").strip())
    return entry if entry.is_absolute() else path / entry


def _common_dir(a_git_dir: Path) -> Path:
    """The repository a git dir belongs to: a worktree's entry names it in ``commondir``."""
    try:
        common = (a_git_dir / "commondir").read_text(encoding="utf-8").strip()
    except OSError:
        return a_git_dir.resolve()
    return (a_git_dir / common).resolve()


def _move_aside(path: Path) -> Path:
    """Rename the left-over folder *path* to a free ``<name>.left-over`` beside it."""
    target = path.with_name(f"{path.name}.left-over")
    count = 1
    while target.exists():
        count += 1
        target = path.with_name(f"{path.name}.left-over-{count}")
    try:
        path.rename(target)
    except OSError as error:
        raise ReviewWorktreeBlocked(
            f"{path} is left over from an earlier review worktree (this repository no longer knows it) and could "
            f"not be moved aside: {error.strerror or error}. Close what has it open, or delete the folder, and "
            "run the review again; or review in this checkout (`ger review --in-place`)"
        ) from error
    return target
