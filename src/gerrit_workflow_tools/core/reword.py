"""Rewriting commit messages of the **local stack**, and nothing else.

``ger reword`` and ``ger serve``'s reword are one rewrite; the message comes from ``-m`` /
``-F``, an editor (:func:`message_to_edit`, :func:`message_from_edit`) or the request. A
message-only rewrite keeps every tree: the commits from the first changed message up are
recreated with ``git commit-tree`` on their new parents, and the branch is moved to the new
tip. The index and the worktree are never touched, so uncommitted changes are no obstacle and
nothing can conflict.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from gerrit_workflow_tools.core.change_id import (
    append_change_id_footer,
    append_change_id_trailer,
    extract_valid_change_id,
    generate_change_id_from_idents,
    strip_change_id_lines,
)
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.git_run import GitError, commit_sha_or_none, git
from gerrit_workflow_tools.core.git_state import current_branch
from gerrit_workflow_tools.core.rebase_state import am_in_progress, rebase_in_progress
from gerrit_workflow_tools.core.reorder import ReorderRefusal, StackRewrite, refuse_unless_on_the_branch
from gerrit_workflow_tools.core.stack import (
    RewriteCommit,
    get_stack_snapshot,
    read_rewrite_rows,
    rev_spec_stack_base_to_end,
)
from gerrit_workflow_tools.core.usage import UsageError


def _commit_tree(cwd: Path | str, tree: str, parents: Sequence[str], message: str, ident_env: dict[str, str]) -> str:
    """Create a commit with *message* (via stdin) and return its SHA."""
    args = ["commit-tree", tree, "-F", "-"]
    for parent in parents:
        args.extend(["-p", parent])
    made = git(*args, cwd=cwd, env=ident_env, input=message, check=False)
    if made.returncode != 0:
        raise GitError(
            "git commit-tree failed", stderr=made.stderr.strip() or made.stdout.strip(), returncode=made.returncode
        )
    return made.stdout.strip()


def rewrite_messages(cwd: Path | str, rows: Sequence[RewriteCommit], messages: Mapping[str, str]) -> str | None:
    """Recreate *rows* (oldest first, the tip last) with *messages* (by commit id) in place of their own.

    Only a commit whose message changes, and the commits above it, get new ids: everything
    else stays as it is, a merged side branch included. Returns the new tip, or ``None`` when
    no row is named in *messages*. Authors, committers and their dates are kept.
    """
    if not rows or not any(row.sha in messages for row in rows):
        return None
    by_sha = {row.sha: row for row in rows}
    new_sha: dict[str, str] = {}
    # Parents before children, whatever order the rows came in: `git log` sorts by date, not by ancestry.
    pending = [rows[-1].sha]
    while pending:
        row = by_sha[pending[-1]]
        waiting = [parent for parent in row.parents if parent in by_sha and parent not in new_sha]
        if waiting:
            pending.extend(waiting)
            continue
        pending.pop()
        parents = [new_sha.get(parent, parent) for parent in row.parents]
        if row.sha in messages or parents != list(row.parents):
            new_sha[row.sha] = _commit_tree(cwd, row.tree, parents, messages.get(row.sha, row.body), row.ident_env)
        else:
            new_sha[row.sha] = row.sha
    return new_sha[rows[-1].sha]


def move_head(cwd: Path | str, new_head: str) -> None:
    """Point the checked-out branch (or a detached ``HEAD``) at *new_head*. Raises :class:`GitError`."""
    branch = current_branch(cwd)
    ref = "HEAD" if branch == "HEAD" else f"refs/heads/{branch}"
    moved = git("update-ref", ref, new_head, cwd=cwd, check=False)
    if moved.returncode != 0:
        raise GitError(
            "git update-ref failed", stderr=moved.stderr.strip() or moved.stdout.strip(), returncode=moved.returncode
        )


def take_back(cwd: Path | str, before: str, after: str, *, why: str) -> bool:
    """Put the checked-out branch back at *before* where it is still at *after*: a message-only rewrite
    (:func:`add_missing_change_ids`) undone, the index and work tree untouched (the trees are the same).
    ``False`` when the branch moved on meanwhile: it is left where it is."""
    branch = current_branch(cwd)
    ref = "HEAD" if branch == "HEAD" else f"refs/heads/{branch}"
    return git("update-ref", "-m", why, ref, before, after, cwd=cwd, check=False).returncode == 0


def reword_commit(
    cwd: Path | str, rev: str, message: str, *, settings: Settings, keep_change_id: bool = False
) -> StackRewrite:
    """Give the local stack commit *rev* (a full or abbreviated id) the commit message *message*.

    A message that lost the commit's **Change-Id** footer gets it back, as the last trailer:
    rewording must not turn the commit into another Gerrit change. One that names another
    Change-Id is taken as written, unless *keep_change_id*: then every ``Change-Id:`` line in
    *message* is dropped and the commit keeps its own (*message* is the text an editor showed
    without it, ``split_change_id_footer``). Raises :class:`ReorderRefusal`, with nothing
    changed, for an empty message (a lone Change-Id dropped counts), a commit that is not in
    the stack, a rebase underway or a detached ``HEAD``.
    """
    refuse_unless_on_the_branch(cwd)
    snapshot = get_stack_snapshot(cwd, settings=settings)
    matches = [c for c in snapshot.commits if rev and c.sha.startswith(rev.lower())]
    if len(matches) != 1:
        raise ReorderRefusal(f"{rev[:12]!r} is not a commit of the local stack (the stack may have changed: reload it)")
    target = matches[0]
    if keep_change_id and target.valid_change_id:
        message = strip_change_id_lines(message)
    text = "\n".join(line.rstrip() for line in message.strip().splitlines())
    if not text:
        raise ReorderRefusal("the commit message is empty; the commit was left as it was")
    if target.valid_change_id and not extract_valid_change_id(text):
        text = append_change_id_trailer(text, target.valid_change_id)
    head = commit_sha_or_none(cwd, "HEAD") or ""
    if text == target.body.strip():
        return StackRewrite(list(snapshot.commits), head)
    rows = read_rewrite_rows(cwd, f"{snapshot.upstream_tip}..HEAD")
    new_head = rewrite_messages(cwd, rows, {target.sha: f"{text}\n"})
    if new_head is None:
        raise ReorderRefusal(f"{target.short_sha} is not reachable from HEAD; the commit was left as it was")
    move_head(cwd, new_head)
    return StackRewrite(list(get_stack_snapshot(cwd, settings=settings).commits), head)


def message_to_edit(body: str, commit: str) -> str:
    """What an editor shows to reword *commit* (``<sha> <subject>``): its message, then git-style comment lines."""
    return (
        f"{body.strip()}\n\n"
        f"# The message of {commit}. Lines starting with '#' are left out.\n"
        "# Unchanged or empty, the commit stays as it is; a message without the Change-Id keeps it.\n"
    )


def message_from_edit(text: str) -> str:
    """The message an editor left: comment lines (``#``) out, as git's default cleanup does."""
    return "\n".join(line for line in text.splitlines() if not line.startswith("#")).strip()


def add_missing_change_ids(cwd: Path, rev: str) -> list[RewriteCommit]:
    """Give every commit of ``upstream_tip..rev`` without a valid Change-Id footer one, as the hook would;
    and a new one to a commit whose Change-Id an older commit of it carries already (the newer of two
    duplicates).

    ``ger change-id --fix``: a message-only rewrite, so uncommitted changes are fine; a merge,
    rebase or ``git am`` underway is a :class:`UsageError`. Returns the commits that got one.
    """
    if commit_sha_or_none(cwd, "MERGE_HEAD"):
        raise UsageError("--fix cannot run during an in-progress merge")
    if rebase_in_progress(cwd):
        raise UsageError("--fix cannot run during an in-progress rebase")
    if am_in_progress(cwd):
        raise UsageError("--fix cannot run during an in-progress `git am`")
    rows = read_rewrite_rows(cwd, rev_spec_stack_base_to_end(cwd, rev))
    ids = [extract_valid_change_id(c.body) for c in rows]
    needs_fix = [c for i, (c, cid) in enumerate(zip(rows, ids, strict=True)) if not cid or cid in ids[:i]]
    messages: dict[str, str] = {}
    for commit in needs_fix:
        base_msg = strip_change_id_lines(commit.body)
        first_parent = commit.parents[0] if commit.parents else None
        cid = generate_change_id_from_idents(commit.committer_ident, first_parent, base_msg)
        messages[commit.sha] = append_change_id_footer(base_msg, cid)
    new_head = rewrite_messages(cwd, rows, messages)
    if new_head is not None:
        try:
            move_head(cwd, new_head)
        except GitError as error:
            raise GitError(f"--fix failed: {error.stderr or error}") from error
    return needs_fix
