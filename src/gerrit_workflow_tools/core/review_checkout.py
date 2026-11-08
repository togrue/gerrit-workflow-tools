"""Review checkout: a change on a ``change-<number>`` branch, staged, and the review state.

Spec: docu/spec/commands/review.md. Gerrit lookups happen in the command; this module only
runs git and keeps ``<git dir>/ger/review.json``, so it never needs a network.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.git_run import GitError, commit_sha_or_none, git, git_out
from gerrit_workflow_tools.core.git_state import checked_out_branch_name, git_dir, repo_toplevel


ReviewMode = Literal["worktree", "branch"]
BRANCH_PREFIX = "change-"
_STATE_FILE = ("ger", "review.json")


class ReviewRefusal(RuntimeError):
    """ger will not go on without the user: their edits or commits are in the way (exit 1)."""


class ReviewUsageError(ValueError):
    """The review cannot do what was asked, whatever the user does next (exit 2)."""


class ReviewChainEndError(RuntimeError):
    """``--next`` / ``--prev`` at the end of the review chain (exit 10)."""


@dataclass(frozen=True)
class PatchsetToReview:
    """One patchset of a change, as Gerrit describes it: what ``open_review`` checks out."""

    number: int
    key: str
    """The change's triplet."""
    change_id: str
    patch_set: int
    commit: str
    subject: str
    target_branch: str


@dataclass(frozen=True)
class CheckoutOptions:
    """How ``open_review`` checks a change out."""

    mode: ReviewMode
    stage: bool
    force: bool = False
    """Move a ``change-<number>`` branch even when it has commits of yours."""


@dataclass(frozen=True)
class ReviewState:
    """What is being reviewed in one worktree. Lives under that worktree's git dir."""

    change: PatchsetToReview
    staged: bool
    mode: ReviewMode
    worktree: str
    created_branches: dict[str, str] = field(default_factory=dict)
    """Branches this review created, and the commit each was created at."""
    return_to: str | None = None
    """In place: the branch (or detached commit) to go back to on ``--done``."""

    @property
    def number(self) -> int:
        """The change number."""
        return self.change.number

    @property
    def key(self) -> str:
        """The change's triplet."""
        return self.change.key

    @property
    def commit(self) -> str:
        """The reviewed patchset's commit."""
        return self.change.commit

    @property
    def patch_set(self) -> int:
        """The reviewed patchset."""
        return self.change.patch_set

    @property
    def branch(self) -> str:
        """The ``change-<number>`` branch the change sits on."""
        return f"{BRANCH_PREFIX}{self.number}"


def changes_ref(number: int, patch_set: int) -> str:
    """Gerrit's ``refs/changes/NN/<number>/<patchset>`` ref."""
    return f"refs/changes/{number % 100:02d}/{number}/{patch_set}"


def _state_path(worktree: Path | str) -> Path | None:
    repo_dir = git_dir(worktree)
    return repo_dir.joinpath(*_STATE_FILE) if repo_dir is not None else None


def load_state(worktree: Path | str) -> ReviewState | None:
    """The review state of *worktree*, or ``None`` when nothing is being reviewed there."""
    path = _state_path(worktree)
    if path is None or not path.is_file():
        return None
    try:
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return ReviewState(**{**raw, "change": PatchsetToReview(**raw["change"])})
    except (ValueError, TypeError, KeyError):
        return None


def save_state(state: ReviewState) -> None:
    """Write *state* under its worktree's git dir."""
    path = _state_path(state.worktree)
    if path is None:
        raise GitError(f"{state.worktree} is not a git worktree")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(state), indent=2) + "\n", encoding="utf-8")


def clear_state(worktree: Path | str) -> None:
    """Forget the review state of *worktree*."""
    path = _state_path(worktree)
    if path is not None and path.is_file():
        path.unlink()


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


def find_review(cwd: Path | str, settings: Settings) -> ReviewState | None:
    """The review going on here, else the one in the review worktree."""
    here = load_state(cwd)
    if here is not None:
        return here
    path = review_worktree_path(cwd, settings)
    return load_state(path) if path.is_dir() else None


def ensure_review_worktree(cwd: Path | str, path: Path, commit: str) -> None:
    """Create the review worktree at *path* (detached at *commit*) unless it is already there."""
    if path.is_dir() and repo_toplevel(path) is not None:
        return
    if path.exists() and any(path.iterdir()):
        raise ReviewUsageError(f"{path} exists and is not a worktree; set ger.reviewWorktree to another path")
    git("worktree", "add", "--detach", str(path), commit, cwd=cwd)


def own_edits(worktree: Path | str, state: ReviewState | None) -> list[str]:
    """Tracked files whose content is not the review's: your edits. Untracked files never count.

    Staged, the index may hold exactly the change; anything else in it, or in the working
    tree, is yours.
    """
    base = state.commit if state is not None and state.staged else "HEAD"
    if commit_sha_or_none(worktree, base) is None:
        return []
    names = set(git_out("diff", "--name-only", base, cwd=worktree).splitlines())
    names |= set(git_out("diff", "--cached", "--name-only", base, cwd=worktree).splitlines())
    return sorted(name for name in names if name)


def _commits_not_in(worktree: Path | str, tip: str, base: str) -> list[str]:
    return git_out("rev-list", f"{base}..{tip}", cwd=worktree).split()


def _unstage(worktree: Path | str, state: ReviewState) -> ReviewState:
    if not state.staged:
        return state
    git("reset", "-q", "--soft", state.commit, cwd=worktree)
    if commit_sha_or_none(worktree, f"refs/heads/{state.branch}") == state.commit:
        git("switch", "-q", state.branch, cwd=worktree)
    return replace(state, staged=False)


def _stage(worktree: Path | str, state: ReviewState) -> ReviewState:
    if state.staged:
        return state
    parent = commit_sha_or_none(worktree, f"{state.commit}^")
    if parent is None:
        raise ReviewUsageError(f"change {state.number} is a root commit: there is no parent to stage it against")
    git("switch", "-q", "--detach", state.commit, cwd=worktree)
    git("reset", "-q", "--soft", parent, cwd=worktree)
    return replace(state, staged=True)


def set_staged(state: ReviewState, staged: bool) -> ReviewState:
    """Stage or unstage the reviewed change, and record it."""
    worktree = state.worktree
    edits = own_edits(worktree, state)
    if edits:
        raise ReviewRefusal("the worktree holds edits of yours: " + ", ".join(edits))
    new = _stage(worktree, state) if staged else _unstage(worktree, state)
    save_state(new)
    return new


def open_review(
    cwd: Path | str, target: PatchsetToReview, *, settings: Settings, options: CheckoutOptions
) -> tuple[ReviewState, str | None]:
    """Fetch *target*, check it out on its ``change-<number>`` branch, stage it, record the review.

    Raises :class:`ReviewRefusal` when the worktree holds your edits, or the branch has your
    commits and ``options.force`` is not set. Never discards an edit. Also returns the old tip
    of a branch ``force`` moved off commits of yours, so they can be recovered.
    """
    mode = options.mode
    remote = settings.gerrit_remote
    worktree = review_worktree_path(cwd, settings) if mode == "worktree" else repo_toplevel(cwd)
    if worktree is None:
        raise GitError("not inside a git worktree")
    git("fetch", "-q", remote, changes_ref(target.number, target.patch_set), cwd=cwd)
    if commit_sha_or_none(cwd, target.commit) is None:
        raise GitError(f"fetching {changes_ref(target.number, target.patch_set)} did not bring {target.commit[:12]}")
    if mode == "worktree":
        ensure_review_worktree(cwd, worktree, target.commit)
    previous = load_state(worktree)
    edits = own_edits(worktree, previous)
    if edits:
        raise ReviewRefusal("the worktree holds edits of yours: " + ", ".join(edits))

    branch = f"{BRANCH_PREFIX}{target.number}"
    created = dict(previous.created_branches) if previous else {}
    moved_from = _claim_branch(worktree, branch, target.commit, created, force=options.force)
    return_to = previous.return_to if previous else _here(worktree) if mode == "branch" else None
    if previous is not None:
        _unstage(worktree, previous)
    git("switch", "-q", "-C", branch, target.commit, cwd=worktree)
    upstream = f"refs/remotes/{remote}/{target.target_branch}"
    if commit_sha_or_none(worktree, upstream) is not None:
        git("branch", "-q", f"--set-upstream-to={remote}/{target.target_branch}", branch, cwd=worktree)
    state = ReviewState(
        change=target, staged=False, mode=mode, worktree=str(worktree), created_branches=created, return_to=return_to
    )
    if options.stage:
        state = _stage(worktree, state)
    save_state(state)
    return state, moved_from


def _claim_branch(
    worktree: Path | str, branch: str, commit: str, created: dict[str, str], *, force: bool
) -> str | None:
    """Check *branch* may move to *commit*; note it in *created* when this review owns it.

    Returns the old tip when it held commits of yours and *force* moves it anyway.
    """
    existing = commit_sha_or_none(worktree, f"refs/heads/{branch}")
    moved_from: str | None = None
    if existing is not None and existing != commit:
        own = _commits_not_in(worktree, existing, commit)
        moved_from = existing if own else None
        if own and not force:
            raise ReviewRefusal(
                f"{branch} has {len(own)} commit(s) the patchset does not: {', '.join(c[:7] for c in own)}. "
                "Pass --force to move it (its tip is printed)"
            )
    if existing is None or branch in created:
        created[branch] = commit
    return moved_from


def _here(worktree: Path | str) -> str | None:
    return checked_out_branch_name(worktree) or commit_sha_or_none(worktree, "HEAD")


def close_review(state: ReviewState) -> tuple[list[str], list[str]]:
    """Unstage, leave the review branches, delete those still at their fetched commit; forget the review.

    Returns ``(deleted, kept)`` branch names. A branch with commits of yours is kept.
    """
    worktree = state.worktree
    edits = own_edits(worktree, state)
    if edits:
        raise ReviewRefusal("the worktree holds edits of yours: " + ", ".join(edits))
    _unstage(worktree, state)
    if state.mode == "branch" and state.return_to:
        is_branch = commit_sha_or_none(worktree, f"refs/heads/{state.return_to}") is not None
        git("switch", "-q", *(() if is_branch else ("--detach",)), state.return_to, cwd=worktree)
    else:
        git("switch", "-q", "--detach", state.commit, cwd=worktree)
    deleted: list[str] = []
    kept: list[str] = []
    for branch, created_at in state.created_branches.items():
        tip = commit_sha_or_none(worktree, f"refs/heads/{branch}")
        if tip is None:
            continue
        if tip != created_at and _commits_not_in(worktree, tip, created_at):
            kept.append(branch)
            continue
        git("branch", "-q", "-D", branch, cwd=worktree)
        deleted.append(branch)
    clear_state(worktree)
    return deleted, kept


def chain_numbers(related: dict[str, Any]) -> list[int]:
    """Open change numbers of a RelatedChangesInfo, base first. Merged and abandoned are skipped."""
    rows = related.get("changes")
    numbers: list[int] = []
    for row in reversed(rows if isinstance(rows, list) else []):
        if not isinstance(row, dict):
            continue
        number, status = row.get("_change_number"), str(row.get("status") or "NEW").upper()
        if isinstance(number, int) and status == "NEW" and number not in numbers:
            numbers.append(number)
    return numbers


def neighbour(chain: list[int], number: int, step: int) -> int:
    """The change *step* places up (``+1``) or down (``-1``) *chain* from *number*."""
    if number not in chain:
        raise ReviewChainEndError(f"change {number} has no review chain to walk")
    index = chain.index(number) + step
    if not 0 <= index < len(chain):
        raise ReviewChainEndError(f"change {number} is the {'top' if step > 0 else 'base'} of its review chain")
    return chain[index]
