"""The **local comment store**: notes and local drafts under ``refs/ger/comments``.

The one owner of that ref (ADR-0010). Layout: ``changes/<triplet>.json`` per change, and
``anchors/<blob>`` pointing at every file version a comment is anchored to (ADR-0011), so
``git gc`` keeps it. Writes use plumbing in a temporary index file and move the ref with a
compare-and-swap, re-applying their one operation on the new tip when another writer won.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from gerrit_workflow_tools.core.anchor_mapping import place_anchor
from gerrit_workflow_tools.core.comment_anchor import Anchor
from gerrit_workflow_tools.core.comment_chains import CommentSelection, local_comment_chain, select_chains
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.gerrit.stack_context import ChangeResolutionError
from gerrit_workflow_tools.core.gerrit_change_status import CommentChain, InlineComment, LocalCommentState
from gerrit_workflow_tools.core.git_run import GitError, git, git_out


REF = "refs/ger/comments"
_ZERO = "0" * 40
_ATTEMPTS = 5
_FALLBACK_IDENTITY = {
    "GIT_AUTHOR_NAME": "ger",
    "GIT_AUTHOR_EMAIL": "ger@localhost",
    "GIT_COMMITTER_NAME": "ger",
    "GIT_COMMITTER_EMAIL": "ger@localhost",
}


@dataclass(frozen=True)
class LocalComment:
    """One note or local draft, keyed by its change and anchored for good."""

    id: str
    state: LocalCommentState
    change: str
    """The change's triplet: which change, never where in it."""
    anchor: Anchor
    message: str
    unresolved: bool
    created: str

    def to_json(self) -> dict[str, Any]:
        """The stored form."""
        return {
            "id": self.id,
            "state": self.state,
            "change": self.change,
            "anchor": self.anchor.to_json(),
            "message": self.message,
            "unresolved": self.unresolved,
            "created": self.created,
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> LocalComment:
        """Parse the stored form; raises ``KeyError``/``TypeError`` on a malformed row."""
        return cls(**{**raw, "anchor": Anchor.from_json(raw["anchor"])})


def new_comment(
    *, state: LocalCommentState, change: str, anchor: Anchor, message: str, unresolved: bool
) -> LocalComment:
    """A fresh local comment with a new id, stamped now."""
    return LocalComment(
        id=uuid.uuid4().hex[:16],
        state=state,
        change=normal_key(change),
        anchor=anchor,
        message=message,
        unresolved=unresolved,
        created=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def normal_key(key: str) -> str:
    """The triplet in one spelling: project unquoted, Change-Id with ``I`` and lower-case hex."""
    parts = unquote(key).split("~")
    if len(parts) == 3 and parts[2][:1] in "iI":
        parts[2] = "I" + parts[2][1:].lower()
    return "~".join(parts)


def _change_path(key: str) -> str:
    return f"changes/{quote(normal_key(key), safe='~')}.json"


class LocalCommentStore:
    """Notes and local drafts of the repository at *cwd*, shared by all its worktrees."""

    def __init__(self, cwd: Path | str) -> None:
        self._cwd = cwd

    def _tip(self) -> str | None:
        shown = git("show-ref", "--verify", "--hash", REF, cwd=self._cwd, check=False)
        return (shown.stdout.strip() or None) if shown.returncode == 0 else None

    def _read(self, tip: str | None, path: str) -> list[LocalComment]:
        if tip is None:
            return []
        shown = git("show", f"{tip}:{path}", cwd=self._cwd, check=False)
        if shown.returncode != 0:
            return []
        rows = json.loads(shown.stdout).get("comments", [])
        return [LocalComment.from_json(row) for row in rows if isinstance(row, dict)]

    def for_change(self, key: str) -> list[LocalComment]:
        """The local comments of change *key*, oldest first."""
        return self._read(self._tip(), _change_path(key))

    def all(self) -> list[LocalComment]:
        """Every local comment in the store."""
        tip = self._tip()
        if tip is None:
            return []
        listed = git("ls-tree", "--name-only", f"{tip}:changes", cwd=self._cwd, check=False)
        names = listed.stdout.splitlines() if listed.returncode == 0 else []
        return [c for name in names if name for c in self._read(tip, f"changes/{name}")]

    def add(self, comment: LocalComment) -> None:
        """Store *comment*, and keep its anchor blob reachable."""

        def append(rows: list[LocalComment]) -> list[LocalComment]:
            return [*rows, comment]

        self._update(comment.change, append, anchor=comment.anchor.blob, message=f"add {comment.id}")

    def remove(self, comment_id: str, *, key: str | None = None) -> LocalComment | None:
        """Delete the comment *comment_id* (looked up on *key* first) and return it; ``None`` if absent."""
        found = next((c for c in self.for_change(key) if c.id == comment_id), None) if key else None
        found = found or next((c for c in self.all() if c.id == comment_id), None)
        if found is None:
            return None

        def drop(rows: list[LocalComment]) -> list[LocalComment]:
            return [c for c in rows if c.id != comment_id]

        self._update(found.change, drop, anchor=None, message=f"remove {comment_id}")
        return found

    def _update(
        self,
        key: str,
        operation: Callable[[list[LocalComment]], list[LocalComment]],
        *,
        anchor: str | None,
        message: str,
    ) -> None:
        """Apply *operation* to the comments of *key* and move the ref, retrying a lost race.

        Each attempt re-reads the tip and re-applies the operation, so a writer that lost
        the race neither drops the winner's change nor applies its own twice.
        """
        path = _change_path(key)
        reason = f"ger comment: {message}"
        for _ in range(_ATTEMPTS):
            tip = self._tip()
            tree = self._tree(tip, path, operation(self._read(tip, path)), anchor)
            commit = self._commit(tree, tip, reason)
            if git("update-ref", "-m", reason, REF, commit, tip or _ZERO, cwd=self._cwd, check=False).returncode == 0:
                return
        raise GitError(f"could not update {REF}: another writer kept moving it ({_ATTEMPTS} attempts)")

    def _tree(self, tip: str | None, path: str, rows: list[LocalComment], anchor: str | None) -> str:
        """*tip*'s tree with *rows* at *path* and *anchor* kept; built in a scratch index, never yours."""
        with tempfile.TemporaryDirectory(prefix="ger-comments-") as scratch:
            env = {"GIT_INDEX_FILE": os.path.join(scratch, "index")}
            git("read-tree", *((tip,) if tip else ("--empty",)), cwd=self._cwd, env=env)
            if rows:
                body = json.dumps({"comments": [row.to_json() for row in rows]}, indent=2, sort_keys=True)
                blob = git("hash-object", "-w", "--stdin", cwd=self._cwd, env=env, input=body).stdout.strip()
                git("update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", cwd=self._cwd, env=env)
            else:
                git("update-index", "--force-remove", path, cwd=self._cwd, env=env)
            if anchor:
                cacheinfo = f"100644,{anchor},anchors/{anchor}"
                git("update-index", "--add", "--cacheinfo", cacheinfo, cwd=self._cwd, env=env)
            return git_out("write-tree", cwd=self._cwd, env=env)

    def _commit(self, tree: str, tip: str | None, message: str) -> str:
        """A commit of *tree* on *tip*."""
        args = ["commit-tree", tree, *(("-p", tip) if tip else ()), "-m", message]
        made = git(*args, cwd=self._cwd, check=False)
        if made.returncode != 0:
            # No user.name / user.email configured: the store still works, written as ger.
            made = git(*args, cwd=self._cwd, env=_FALLBACK_IDENTITY)
        return made.stdout.strip()


def local_chains(cwd: Path | str, key: str, commit: str | None, *, comments: CommentSelection) -> list[CommentChain]:
    """The notes and local drafts of change *key* as chains, shown on *commit*, selected by *comments*.

    Each sits where its anchor maps to in *commit*'s version of the file
    (:func:`~gerrit_workflow_tools.core.anchor_mapping.place_anchor`); nothing is written back.
    """
    chains: list[CommentChain] = []
    for comment in LocalCommentStore(cwd).for_change(key):
        anchor = place_anchor(cwd, comment.anchor, commit)
        inline = InlineComment(
            path=anchor.path,
            line=anchor.line,
            message=comment.message,
            comment_id=comment.id,
            unresolved=comment.unresolved,
            local=comment.state,
        )
        chains.append(local_comment_chain(comment.id, inline, anchor))
    return select_chains(chains, comments)


def local_chains_of(
    service: GerritService, change: str, commit: str | None, *, comments: CommentSelection
) -> list[CommentChain]:
    """:func:`local_chains` for *change* (a triplet, or a Change-Id keyed through the stack context).

    A Change-Id with no stack context to key it by has no local comments to show.
    """
    try:
        key = service.cache_key(change)
    except ChangeResolutionError:
        return []
    return local_chains(service.cwd, key, commit, comments=comments)
