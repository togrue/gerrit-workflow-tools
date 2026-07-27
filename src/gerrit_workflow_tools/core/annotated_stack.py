"""The annotated stack: local commits carrying Gerrit overlay state and attention.

This is the shape `ger log`, `ger show`, `ger edit --first-attention-commit` and the rebase
enricher all want. It used to live inside ``cli_log``, so the others either imported upward
into a command module or re-derived it — the enricher had its own copy of the chain-blocking
rule, and ``ger show`` derived attention a third way.

Two entry points, because only some callers start from a revision range:

* :func:`annotate` takes commit rows from anywhere — a range, a rebase todo, a single
  resolved changeish — and returns them overlaid and annotated.
* :func:`load_annotated_stack` resolves a range first, and additionally derives multi-branch
  resolution notes from the local cache.

Nothing here prompts or prints. Revision ranges may reference an ``@{upstream}`` that is not
configured; :func:`branches_needing_upstream` reports which branches those are, and the
caller decides whether to prompt, fail, or carry on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from gerrit_workflow_tools.core.change_id import validate_change_id_value
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.change_resolution import (
    ChangeResolutionError,
    format_resolution_note,
    resolution_from_change_rows,
    resolve_stack_context,
)
from gerrit_workflow_tools.core.gerrit.rest import GerritRest
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.gerrit_change_status import (
    CommitStatusInput,
    LogCommit,
    annotate_attention,
)
from gerrit_workflow_tools.core.git_state import current_branch, resolve_working_branch
from gerrit_workflow_tools.core.stack import commits_in_range

_UPSTREAM_TOKEN_RE = re.compile(r"(?P<branch>[^\s@]+)?@\{upstream\}")


@dataclass(frozen=True)
class AnnotatedStack:
    """Commits in a revision range, overlaid with Gerrit state and annotated with attention."""

    commits: list[LogCommit] = field(default_factory=list)
    notes_by_sha: dict[str, str] = field(default_factory=dict)
    """Multi-branch resolution notes, keyed by commit SHA. Only commits whose Change-Id
    matched on more than one branch appear here."""

    def __bool__(self) -> bool:
        return bool(self.commits)


def resolve_rev_range(cwd: Path | str | None, arg_rev_range: str | None, *, settings: Settings) -> str:
    """Return the revision range to inspect.

    A bare ref becomes ``<ref>@{upstream}..<ref>``; a range is taken as given. With no
    argument, the working branch's upstream range is used. Raises :class:`GitError` when the
    working branch cannot be determined.
    """
    if arg_rev_range:
        if ".." in arg_rev_range:
            return arg_rev_range
        return f"{arg_rev_range}@{{upstream}}..{arg_rev_range}"
    branch = resolve_working_branch(cwd, settings=settings) or current_branch(cwd)
    if branch == "HEAD":
        return "@{upstream}..HEAD"
    return f"{branch}@{{upstream}}..{branch}"


def branches_needing_upstream(cwd: Path | str | None, rev_range: str, *, settings: Settings) -> list[str]:
    """Return branch names in *rev_range* whose ``@{upstream}`` the caller must ensure exists.

    Reports only; resolving an upstream is interactive and stays with the caller.
    """
    current = resolve_working_branch(cwd, settings=settings) or current_branch(cwd)
    required: list[str] = []
    seen: set[str] = set()
    for match in _UPSTREAM_TOKEN_RE.finditer(rev_range):
        branch = (match.group("branch") or current).lstrip(".")
        if branch in ("HEAD", ""):
            branch = current
        if branch in seen:
            continue
        seen.add(branch)
        required.append(branch)
    return required


def commit_rows_in_range(
    cwd: Path | str | None,
    rev_range: str,
    *,
    first_parent: bool = True,
) -> list[CommitStatusInput]:
    """Return overlay input rows for the local commits in *rev_range* (oldest first)."""
    return [
        CommitStatusInput(
            sha=c.sha,
            short_sha=c.short_sha,
            summary=c.subject,
            # Validated here: a malformed footer must never become a Gerrit query.
            change_id=c.change_id if validate_change_id_value(c.change_id)[0] else None,
        )
        for c in commits_in_range(cwd, rev_range, first_parent=first_parent)
    ]


def annotate(
    rows: list[CommitStatusInput],
    *,
    service: GerritService,
    cwd: Path | str | None,
) -> list[LogCommit]:
    """Overlay Gerrit state onto *rows* and annotate attention, oldest first.

    Order matters: attention includes chain-blocking, so *rows* must be in stack order for
    an earlier commit to block the ones after it.
    """
    commits = service.fetch_gerrit_data(rows, cwd=cwd)
    annotate_attention(commits)
    return commits


def _multi_branch_notes(
    service: GerritService,
    rows: list[CommitStatusInput],
    cwd: Path | str | None,
) -> dict[str, str]:
    """Resolution notes for Change-Ids that exist on more than one branch.

    Derived from what the overlay already stored locally — never a per-Change-Id re-query.
    """
    footer_ids = [row.change_id for row in rows if row.change_id]
    if not footer_ids:
        return {}
    stack = resolve_stack_context(cwd, settings=service.settings)
    by_footer = service.changes.find_by_footer_change_ids(footer_ids)
    notes: dict[str, str] = {}
    for row in rows:
        if not row.change_id:
            continue
        try:
            resolution = resolution_from_change_rows(
                row.change_id,
                by_footer.get(row.change_id, []),
                push_branch=stack.push_branch,
                explicit_target=False,
            )
        except ChangeResolutionError:
            continue
        note = format_resolution_note(resolution)
        if note:
            notes[row.sha] = note
    return notes


def load_annotated_stack(
    cwd: Path,
    rev_range: str,
    *,
    settings: Settings,
    first_parent: bool = True,
    gerrit: GerritRest | None = None,
) -> AnnotatedStack:
    """Load *rev_range*, overlay Gerrit state, annotate attention, and derive notes.

    Defaults to first-parent traversal, matching Gerrit's relation-chain semantics and
    :func:`commit_rows_in_range`. Pass ``first_parent=False`` for ``--follow-merges``.

    Returns an empty stack when the range holds no commits — deciding whether that is an
    error is the caller's business. Raises :class:`GitError` for git failures,
    :class:`ValueError` when ``gerrit.webUrl`` is unset, and
    :class:`ChangeResolutionError` / :class:`GerritApiError` for Gerrit failures.
    """
    rows = commit_rows_in_range(cwd, rev_range, first_parent=first_parent)
    if not rows:
        return AnnotatedStack()
    service = GerritService.from_cwd(cwd, settings=settings, rest=gerrit)
    commits = annotate(rows, service=service, cwd=cwd)
    return AnnotatedStack(commits=commits, notes_by_sha=_multi_branch_notes(service, rows, cwd))
