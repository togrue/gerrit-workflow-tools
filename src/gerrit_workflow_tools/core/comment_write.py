"""Writing your own comments: a Gerrit **draft**, a **local draft** or a **note**.

Starting a chain, and editing or discarding a comment of yours that is not published yet.
``ger comment`` and ``POST /comments/new``, ``/comments/edit`` and ``/comments/discard`` all
come here; they differ only in how they parse their input and show the answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gerrit_workflow_tools.core.comment_anchor import (
    Anchor,
    CommentLocation,
    anchor_at,
    close_unchecked,
    file_lines,
    fit_to_file,
    parse_location,
    repository_path,
)
from gerrit_workflow_tools.core.comment_target import CommentTarget
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.gerrit.stack_context import ChangeResolutionError
from gerrit_workflow_tools.core.gerrit_change_status import (
    CommentState,
    LogCommit,
    PatchsetStatus,
    gerrit_inline_comment_url,
)
from gerrit_workflow_tools.core.local_comments import LocalCommentStore, new_comment
from gerrit_workflow_tools.core.usage import UsageError


@dataclass(frozen=True)
class WrittenComment:
    """A comment chain just started, wherever it went."""

    state: CommentState
    id: str | None
    """The comment's id; Gerrit may leave it out of a draft's answer."""
    change: str
    commit: str | None
    location: CommentLocation
    patch_set: int | None
    """The Gerrit patchset a draft is on; ``None`` for a note or local draft."""
    unresolved: bool
    url: str | None


def locate(cwd: Path, raw: str, target: CommentTarget) -> CommentLocation:
    """*raw* (the LOCATION grammar) checked against the target's commit when it is here.

    Raises :class:`~gerrit_workflow_tools.core.comment_anchor.LocationError` for a location that
    does not fit the file.
    """
    location = parse_location(raw)
    if location.on_gerrit_only:  # no file of the commit: Gerrit checks it
        return location
    location = repository_path(cwd, location)
    if target.commit is None:
        return close_unchecked(location)
    return fit_to_file(location, file_lines(cwd, target.commit, location.path), commit=target.commit)


def default_state(target: CommentTarget, *, gerrit: bool) -> CommentState:
    """A draft on the patchset Gerrit has; a local draft when Gerrit has no patchset (or is not configured)."""
    return "draft" if gerrit and target.on_gerrit else "local-draft"


def _anchor(cwd: Path, target: CommentTarget, location: CommentLocation) -> Anchor:
    """The anchor of *location* in the target's commit, which a local comment needs to be here."""
    commit = target.commit
    lines = file_lines(cwd, commit, location.path) if commit else None
    if commit is None or lines is None:
        raise ChangeResolutionError(f"{target.key}: its commit is not here, and a local comment is anchored to it")
    return anchor_at(cwd, commit, location, lines)


def write_comment(
    cwd: Path,
    service: GerritService | None,
    target: CommentTarget,
    location: CommentLocation,
    message: str,
    *,
    state: CommentState,
    unresolved: bool,
) -> WrittenComment:
    """Start a chain at *location*: a draft on Gerrit, or a note / local draft in the local comment store.

    One on the change or its commit message is a draft on Gerrit only: no local comment can be anchored there.
    """
    if location.on_gerrit_only and state != "draft":
        raise UsageError(
            f"a comment on {location.describe()} is a draft on Gerrit: a note or local draft goes on a file"
        )
    if state == "draft":
        if service is None or not target.on_gerrit or target.patch_set is None:
            raise ChangeResolutionError(f"{target.key}: Gerrit has no patchset of it to write a draft on")
        body = {**location.as_gerrit(), "message": message, "unresolved": unresolved}
        draft_id = service.comments.create_draft(target.key, target.patch_set, body)
        url = gerrit_inline_comment_url(target.change_url, draft_id)
        return WrittenComment("draft", draft_id, target.key, target.commit, location, target.patch_set, unresolved, url)
    if state == "published":
        raise ValueError("a new comment is published with the review, not written published")
    comment = new_comment(
        state=state, change=target.key, anchor=_anchor(cwd, target, location), message=message, unresolved=unresolved
    )
    LocalCommentStore(cwd).add(comment)
    return WrittenComment(state, comment.id, target.key, target.commit, comment.anchor.location, None, unresolved, None)


@dataclass(frozen=True)
class ChangedComment:
    """One of your unpublished comments, as an edit or a discard found it."""

    state: CommentState
    id: str
    change: str
    where: str
    """``PATH`` or ``PATH:LINE``, as the comment says it."""
    message: str
    """What it said before: the text an edit replaced, or a discard deleted."""


def _where(path: str, line: int | None) -> str:
    return CommentLocation(path=path, line=line).describe()


def _not_yours(target: CommentTarget, comment_id: str) -> ChangeResolutionError:
    return ChangeResolutionError(f"no comment {comment_id} of yours on {target.key}")


def edit_comment(
    cwd: Path,
    service: GerritService | None,
    target: CommentTarget,
    comment_id: str,
    message: str,
    *,
    unresolved: bool | None = None,
) -> ChangedComment:
    """Give your unpublished comment *comment_id* the text *message*; it stays where it is.

    A note or local draft in the local comment store, else your Gerrit draft with that id on
    the target change. A published comment is nobody's to edit. *unresolved* ``None`` keeps its flag.
    """
    local = LocalCommentStore(cwd).edit(comment_id, message, unresolved=unresolved, key=target.key)
    if local is not None:
        return ChangedComment(local.state, comment_id, local.change, local.anchor.location.describe(), local.message)
    was = None
    if service and target.change_url:
        was = service.comments.edit_draft(target.key, comment_id, message, unresolved=unresolved)
    if was is None:
        raise _not_yours(target, comment_id)
    return ChangedComment("draft", comment_id, target.key, _where(was.path, was.line), was.message)


def discard_comment(cwd: Path, service: GerritService | None, target: CommentTarget, comment_id: str) -> ChangedComment:
    """Delete your local comment, else your Gerrit draft, with *comment_id*; the answer keeps its text."""
    local = LocalCommentStore(cwd).remove(comment_id, key=target.key)
    if local is not None:
        return ChangedComment(local.state, comment_id, local.change, local.anchor.location.describe(), local.message)
    removed = service.comments.discard_draft(target.key, comment_id) if service and target.change_url else None
    if removed is None:
        raise _not_yours(target, comment_id)
    return ChangedComment("draft", comment_id, target.key, _where(removed.path, removed.line), removed.message)


_PATCHSETS_GERRIT_HAS = frozenset({PatchsetStatus.ACTIVE, PatchsetStatus.OUTDATED})


def new_comment_state(commit: LogCommit, *, is_local: bool) -> CommentState:
    """What a new comment on *commit* becomes by default (:func:`default_state`), from the overlay alone.

    A draft when Gerrit has this very commit as a patchset (or, for a change that is only on
    Gerrit, its current one); a local draft when it was never pushed or was rewritten here.
    """
    if not commit.pushed:
        return "local-draft"
    if not is_local or commit.patchset_status in _PATCHSETS_GERRIT_HAS:
        return "draft"
    return "local-draft"
