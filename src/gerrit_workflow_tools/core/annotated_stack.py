"""The annotated stack: local commits carrying Gerrit overlay state and attention.

This is the shape `ger log`, `ger show`, `ger edit --first-attention-commit` and the rebase
enricher all want.

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

from gerrit_workflow_tools.core.attention import annotate_attention
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.change_resolution import (
    ChangeResolutionError,
    format_resolution_note,
    resolution_from_change_rows,
)
from gerrit_workflow_tools.core.gerrit.rest import GerritRest
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.gerrit.stack_context import resolve_stack_context, stack_project
from gerrit_workflow_tools.core.gerrit_change_status import LogCommit, PatchsetStatus
from gerrit_workflow_tools.core.git_run import GitError, git
from gerrit_workflow_tools.core.git_state import resolve_branch_for_branch_config
from gerrit_workflow_tools.core.stack import Commit, commits_in_range, upstream_tracking_tip_and_display


_UPSTREAM_TOKEN_RE = re.compile(r"(?P<branch>[^\s@]+)?@\{upstream\}")


@dataclass(frozen=True)
class AnnotatedStack:
    """Commits in a revision range, overlaid with Gerrit state and annotated with attention."""

    commits: list[LogCommit] = field(default_factory=list)
    rows: list[Commit] = field(default_factory=list)
    """The **commit rows** the overlay started from, same order as :attr:`commits`."""
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
    branch = resolve_branch_for_branch_config(cwd, settings=settings)
    if branch == "HEAD":
        return "@{upstream}..HEAD"
    return f"{branch}@{{upstream}}..{branch}"


def branches_needing_upstream(cwd: Path | str | None, rev_range: str, *, settings: Settings) -> list[str]:
    """Return branch names in *rev_range* whose ``@{upstream}`` the caller must ensure exists.

    Reports only; resolving an upstream is interactive and stays with the caller.
    """
    current = resolve_branch_for_branch_config(cwd, settings=settings)
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


def annotate(
    rows: list[Commit],
    *,
    service: GerritService,
    cwd: Path | str | None,
    fetch_ci_pipelines: bool = False,
    on_target: frozenset[str] = frozenset(),
) -> list[LogCommit]:
    """Overlay Gerrit state onto *rows* and annotate attention, oldest first.

    Order matters: attention includes chain-blocking, so *rows* must be in stack order for
    an earlier commit to block the ones after it. *on_target* names commits already on the
    target branch: with no change to report, they count as merged rather than not pushed.
    """
    commits = service.fetch_gerrit_data(rows, cwd=cwd, fetch_ci_pipelines=fetch_ci_pipelines)
    for commit in commits:
        if commit.sha in on_target and commit.patchset_status == PatchsetStatus.ABSENT:
            commit.patchset_status = PatchsetStatus.MERGED_SAME
            commit.merged_equivalent = True
    annotate_attention(
        commits,
        cwd=cwd,
        project=stack_project(cwd, settings=service.settings),
        settings=service.settings,
        web_base=service.web_base,
    )
    return commits


def _commits_on_target(
    cwd: Path | str | None, rows: list[Commit], rev_range: str, *, settings: Settings
) -> frozenset[str]:
    """SHAs in *rows* the upstream already contains. Only an explicit range can name any."""
    if "@{upstream}" in rev_range:
        return frozenset()
    try:
        tip, _display = upstream_tracking_tip_and_display(cwd, settings=settings)
    except GitError:
        return frozenset()
    upstream_shas = git("rev-list", f"{rows[0].sha}^..{tip}", cwd=cwd, check=False).stdout.split()
    return frozenset(upstream_shas) & {row.sha for row in rows}


def _multi_branch_notes(
    service: GerritService,
    rows: list[Commit],
    cwd: Path | str | None,
) -> dict[str, str]:
    """Resolution notes for Change-Ids that exist on more than one branch.

    Derived from what the overlay already stored locally — never a per-Change-Id re-query.
    """
    footer_ids = [row.valid_change_id for row in rows if row.valid_change_id]
    if not footer_ids:
        return {}
    stack = resolve_stack_context(cwd, settings=service.settings)
    by_footer = service.changes.find_by_footer_change_ids(footer_ids)
    notes: dict[str, str] = {}
    for row in rows:
        change_id = row.valid_change_id
        if not change_id:
            continue
        try:
            resolution = resolution_from_change_rows(
                change_id,
                by_footer.get(change_id, []),
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
    fetch_ci_pipelines: bool = False,
) -> AnnotatedStack:
    """Load *rev_range*, overlay Gerrit state, annotate attention, and derive notes.

    Defaults to first-parent traversal, matching Gerrit's relation-chain semantics. Pass
    ``first_parent=False`` for ``--follow-merges``.

    Returns an empty stack when the range holds no commits — deciding whether that is an
    error is the caller's business. Raises :class:`GitError` for git failures,
    :class:`ValueError` when ``gerrit.webUrl`` is unset, and
    :class:`ChangeResolutionError` / :class:`GerritApiError` for Gerrit failures.
    """
    rows = commits_in_range(cwd, rev_range, first_parent=first_parent)
    if not rows:
        return AnnotatedStack()
    service = GerritService.from_cwd(cwd, settings=settings, rest=gerrit)
    on_target = _commits_on_target(cwd, rows, rev_range, settings=settings)
    commits = annotate(rows, service=service, cwd=cwd, fetch_ci_pipelines=fetch_ci_pipelines, on_target=on_target)
    return AnnotatedStack(commits=commits, rows=rows, notes_by_sha=_multi_branch_notes(service, rows, cwd))
