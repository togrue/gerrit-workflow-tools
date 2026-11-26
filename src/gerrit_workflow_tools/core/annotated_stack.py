"""The annotated stack: local commits carrying Gerrit overlay state and attention.

This is the shape `ger log`, `ger show`, `ger edit --first-attention-commit` and the rebase
enricher all want.

Two entry points, because only some callers start from a revision range:

* :func:`annotate` takes commit rows from anywhere — a range, a rebase todo, a single
  resolved changeish — and returns them overlaid and annotated.
* :func:`load_annotated_stack` resolves a range first, and additionally derives multi-branch
  resolution notes from the local cache.

:func:`load_stack_overview` adds what ``ger log`` and ``GET /stack`` say about the stack as a
whole, each computed once: the ready boundary, the rebase underway, how far the upstream moved.

Nothing here prompts or prints. Revision ranges may reference an ``@{upstream}`` that is not
configured; ``rev_range.branches_needing_upstream`` reports which branches those are, and the
caller decides whether to prompt, fail, or carry on.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path

from gerrit_workflow_tools.core.attention import Attention, annotate_attention, chain_blocker
from gerrit_workflow_tools.core.gerrit.change_resolution import ChangeResolutionError, format_resolution_note
from gerrit_workflow_tools.core.gerrit.narrowing import resolution_from_change_rows
from gerrit_workflow_tools.core.gerrit.rest import GerritUnavailableError
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.gerrit_change_status import LogCommit, PatchsetStatus, local_log_commit
from gerrit_workflow_tools.core.git_run import GitError, git
from gerrit_workflow_tools.core.ready_strategy import blocked_shas_for_stack, change_ids_push_adds
from gerrit_workflow_tools.core.rebase_state import RebaseProgress, rebase_progress
from gerrit_workflow_tools.core.rebasing_stack import RebasingStack, load_rebasing_stack
from gerrit_workflow_tools.core.rev_range import resolve_rev_range
from gerrit_workflow_tools.core.run_context import RunContext
from gerrit_workflow_tools.core.stack import Commit, commits_in_range, upstream_tracking_tip_and_display
from gerrit_workflow_tools.core.upstream import NeedsUpstreamError
from gerrit_workflow_tools.core.upstream_moved import UpstreamMoved, merged_same, upstream_moved


@dataclass(frozen=True)
class AnnotatedStack:
    """Commits in a revision range, overlaid with Gerrit state and annotated with attention."""

    commits: list[LogCommit] = field(default_factory=list)
    rows: list[Commit] = field(default_factory=list)
    """The **commit rows** the overlay started from, same order as :attr:`commits`."""
    notes_by_sha: dict[str, str] = field(default_factory=dict)
    """Multi-branch resolution notes, keyed by commit SHA. Only commits whose Change-Id
    matched on more than one branch appear here."""
    rebase: RebasingStack | None = None
    """While a rebase is underway, the run's local stack as it stands (:class:`RebasingStack`):
    :attr:`commits` are its commits, and its steps say where the rebase stands on each."""
    gerrit_unavailable: GerritUnavailableError | None = None
    """Why Gerrit could not be asked, when it could not. Every commit then carries
    :attr:`PatchsetStatus.UNKNOWN` rather than a guess; the caller shows the local state and
    reports this, once."""
    blocker: LogCommit | None = None
    """The commit that holds the chain back (:attr:`LogCommit.blocks_chain`), which may be one
    below :attr:`commits` when they were judged in the local stack (``core/annotated_targets.py``)."""
    around: list[LogCommit] = field(default_factory=list)
    """The local stack :attr:`commits` were judged within (``core/annotated_targets.py``), annotated
    as well; empty when they were judged on their own."""

    def __bool__(self) -> bool:
        return bool(self.commits)

    def require_gerrit(self) -> list[LogCommit]:
        """:attr:`commits`, for a caller that has nothing to show without Gerrit; else raise why."""
        if self.gerrit_unavailable is not None:
            raise self.gerrit_unavailable
        return self.commits


def annotate(
    rows: list[Commit],
    *,
    service: GerritService,
    cwd: Path | str | None,
    fetch_ci_pipelines: bool = False,
    on_target: frozenset[str] = frozenset(),
    triplets: Sequence[str | None] | None = None,
    overlay: bool = True,
) -> AnnotatedStack:
    """Overlay Gerrit state onto *rows* and annotate attention, oldest first.

    Order matters: attention includes chain-blocking, so *rows* must be in stack order for
    an earlier commit to block the ones after it. *on_target* names commits already on the
    target branch: with no change to report, they count as merged rather than not pushed.
    *triplets* names, per row, a change to overlay instead of the push branch's (see
    :meth:`GerritService.fetch_gerrit_data`).

    When Gerrit cannot be asked at all, the rows come back with an unknown Gerrit state and
    the reason in :attr:`AnnotatedStack.gerrit_unavailable`: shown, never guessed. With *overlay*
    off (no push branch to find the changes on), they come back with an unknown Gerrit state too.
    """
    unavailable: GerritUnavailableError | None = None
    commits = [local_log_commit(row, PatchsetStatus.UNKNOWN) for row in rows]
    try:
        if overlay:
            commits = service.fetch_gerrit_data(rows, cwd=cwd, fetch_ci_pipelines=fetch_ci_pipelines, triplets=triplets)
    except GerritUnavailableError as error:
        unavailable = error
    for commit in commits:
        if commit.sha in on_target and commit.patchset_status in (PatchsetStatus.ABSENT, PatchsetStatus.UNKNOWN):
            commit.patchset_status = PatchsetStatus.MERGED_SAME
            commit.merged_equivalent = True
    annotate_attention(
        commits,
        root=service.repo.root,
        project=_project(service),
        settings=service.settings,
        web_base=service.web_base,
    )
    return AnnotatedStack(
        commits=commits, rows=list(rows), gerrit_unavailable=unavailable, blocker=chain_blocker(commits)
    )


def _commits_on_target(run: RunContext, rows: list[Commit], rev_range: str) -> frozenset[str]:
    """SHAs in *rows* the upstream already contains. Only an explicit range can name any."""
    if "@{upstream}" in rev_range:
        return frozenset()
    try:
        tip, _display = upstream_tracking_tip_and_display(run.cwd, run.repo.stack_branch, settings=run.settings)
    except (GitError, NeedsUpstreamError):  # a detached HEAD has no upstream: nothing counts as merged
        return frozenset()
    upstream_shas = git("rev-list", f"{rows[0].sha}^..{tip}", cwd=run.cwd, check=False).stdout.split()
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
    stack = service.stack_context()
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


def _project(service: GerritService) -> str:
    """The run's project, else ``ger.project`` when the stack has no context to give one."""
    try:
        return service.stack_context().project
    except ChangeResolutionError:
        return service.settings.gerrit_project or ""


def _stack_rows(
    run: RunContext, rev_range: str | None, *, first_parent: bool
) -> tuple[list[Commit], RebasingStack | None]:
    """The commit rows of *rev_range*; with none, the run's local stack, the rebasing one while a rebase is underway."""
    branch = run.repo.stack_branch
    progress = rebase_progress(run.cwd, repo_dir=run.worktree.repository_dir) if rev_range is None else None
    # A rebase that keeps no todo (rebase-apply) says nothing of its commits: the branch as it was.
    if progress is not None and progress.keeps_todo:
        # A detached HEAD has no upstream: the stack is then what the rebase replays, above its onto.
        base = f"{branch}@{{upstream}}" if branch != "HEAD" else progress.onto or "@{upstream}"
        rebasing = load_rebasing_stack(run.cwd, base, progress, first_parent=first_parent)
        return list(rebasing.commits), rebasing
    rows = commits_in_range(run.cwd, rev_range or resolve_rev_range(None, branch=branch), first_parent=first_parent)
    return rows, None


def load_annotated_stack(
    run: RunContext,
    rev_range: str | None,
    *,
    first_parent: bool = True,
    fetch_ci_pipelines: bool = False,
    gerrit: bool = True,
) -> AnnotatedStack:
    """Load *rev_range*, overlay Gerrit state (unless *gerrit* is off), annotate attention, and derive notes.

    With no *rev_range*, the run's **local stack**: while a rebase is underway, the commits it
    replayed so far as they are now, then those still to replay as they were
    (:attr:`AnnotatedStack.rebase`). The Gerrit overlay finds each by its Change-Id either way.

    Defaults to first-parent traversal, matching Gerrit's relation-chain semantics. Pass
    ``first_parent=False`` for ``--follow-merges``.

    Returns an empty stack when the range holds no commits — deciding whether that is an
    error is the caller's business. Raises :class:`GitError` for git failures and
    :class:`ValueError` when ``ger.webUrl`` is unset. A Gerrit that cannot be asked at all
    does not raise: see :attr:`AnnotatedStack.gerrit_unavailable`.
    """
    cwd, service = run.cwd, run.gerrit
    rows, rebasing = _stack_rows(run, rev_range, first_parent=first_parent)
    if not rows:
        return AnnotatedStack(rebase=rebasing)
    on_target = _commits_on_target(run, rows, rev_range) if rev_range else frozenset()
    # A detached HEAD's rebase has no working branch, so no push branch to find its changes on.
    overlay = gerrit and (rebasing is None or run.repo.working_branch is not None)
    annotated = annotate(
        rows, service=service, cwd=cwd, fetch_ci_pipelines=fetch_ci_pipelines, on_target=on_target, overlay=overlay
    )
    annotated = replace(annotated, rebase=rebasing)
    if annotated.gerrit_unavailable is not None or not overlay:
        return annotated
    return replace(annotated, notes_by_sha=_multi_branch_notes(service, rows, cwd))


@dataclass(frozen=True)
class StackOverview:
    """The **annotated stack** with what is said about it as a whole: ``ger log``'s answer, and ``GET /stack``'s."""

    view: AnnotatedStack
    blocked: frozenset[str]
    """The commits the **ready boundary** holds back: it and everything above. Empty without commits."""
    progress: RebaseProgress | None
    """The rebase underway, or ``None``."""
    moved: UpstreamMoved | None
    """How far the upstream moved past the local stack; ``None`` for a revision range, during a
    rebase (that one is finished first), or without an upstream."""
    whole_stack: bool = False
    """The commits are the local stack of the checked-out branch, no rebase underway: what
    ``ger checkout --update`` alone brings to Gerrit's current patchsets."""
    adds_change_id: frozenset[str] = frozenset()
    """Commits ``ger push`` gives a Change-Id before it sends them (``change_ids_push_adds``)."""

    @property
    def ready(self) -> int:
        """How many commits sit below the ready boundary: the ones ``ger push`` pushes."""
        return len(self.view.commits) - len(self.blocked)

    @property
    def to_upload(self) -> list[LogCommit]:
        """The commits ``ger push`` uploads: below the ready boundary, and new to Gerrit or ahead of it.

        A commit Gerrit already has as its current patch set is pushed but uploads nothing; a
        diverged one ``ger push`` refuses to put over Gerrit's.
        """
        return [
            c
            for c in self.view.commits
            if c.sha not in self.blocked
            and not c.abandoned
            and (
                c.patchset_status == PatchsetStatus.ABSENT
                or (c.patchset_status == PatchsetStatus.NEWER and not c.diverged)
            )
        ]

    def next_steps(self) -> list[NextStep]:
        """What to do next, one step per state the stack is in that has a ger command for it."""
        commits = self.view.commits
        moved = self.moved
        steps = [NextStep(NextStepKind.UPSTREAM_MOVED)] if moved and moved.behind else []
        merged = [c for c in commits if c.patchset_status in (PatchsetStatus.MERGED_SAME, PatchsetStatus.MERGED_DRIFT)]
        if merged and not steps:
            steps.append(NextStep(NextStepKind.MERGED, (merged[0],)))
        for kind, hit in (
            (NextStepKind.ABANDONED, [c for c in commits if c.abandoned]),
            (NextStepKind.DIVERGED, [c for c in commits if Attention.DIVERGED in c.attention_reasons and c.number]),
        ):
            if hit:
                steps.append(NextStep(kind, (hit[0],)))
        if outdated := [c for c in commits if Attention.OUTDATED_PATCHSET in c.attention_reasons and c.number]:
            # One command takes them all on the stack; elsewhere, one change at a time, the lowest first.
            taken = outdated if self.whole_stack else outdated[:1]
            steps.append(NextStep(NextStepKind.OUTDATED, tuple(taken), whole_stack=self.whole_stack))
        if upload := self.to_upload:
            steps.append(NextStep(NextStepKind.UPLOAD, tuple(upload)))
        if wip := [c for c in commits if c.pushed and Attention.WIP in c.attention_reasons]:
            steps.append(
                NextStep(NextStepKind.WIP, tuple(wip), beyond_boundary=any(c.sha in self.blocked for c in wip))
            )
        if blocker := chain_blocker(commits):
            steps.append(NextStep(NextStepKind.CHAIN_BLOCKED, (blocker,)))
        return steps


class NextStepKind(str, Enum):
    """A state of the stack that has a ger command for it (the **next steps** ``ger log`` closes with)."""

    UPSTREAM_MOVED = "upstream-moved"
    """The upstream moved on: :attr:`StackOverview.moved` says how, and the rebase onto it."""
    MERGED = "merged"
    ABANDONED = "abandoned"
    DIVERGED = Attention.DIVERGED
    OUTDATED = Attention.OUTDATED_PATCHSET
    UPLOAD = "upload"
    """Commits ``ger push`` uploads (:attr:`StackOverview.to_upload`)."""
    WIP = Attention.WIP
    CHAIN_BLOCKED = Attention.CHAIN_BLOCKED
    """The commit that holds the chain back (:attr:`LogCommit.blocks_chain`)."""


@dataclass(frozen=True)
class NextStep:
    """One next step: what state, and the commits it is about (the first one only, where one command fixes one)."""

    kind: NextStepKind
    commits: tuple[LogCommit, ...] = ()
    beyond_boundary: bool = False
    """WIP only: some of them sit above the ready boundary, so only ``ger push --all`` reaches them."""
    whole_stack: bool = False
    """OUTDATED only: :attr:`commits` are every outdated commit, and ``ger checkout --update`` alone
    updates them (:attr:`StackOverview.whole_stack`); else the lowest, by its change."""


def load_stack_overview(
    run: RunContext,
    rev_range: str | None = None,
    *,
    first_parent: bool = True,
    fetch_ci_pipelines: bool = False,
    gerrit: bool = True,
) -> StackOverview:
    """:func:`load_annotated_stack`, with the ready boundary ``ger push`` stops at, the rebase and the upstream."""
    loaded = {"first_parent": first_parent, "fetch_ci_pipelines": fetch_ci_pipelines, "gerrit": gerrit}
    view = load_annotated_stack(run, rev_range, **loaded)
    settings = run.settings
    progress = view.rebase.rebase if view.rebase else rebase_progress(run.cwd, repo_dir=run.worktree.repository_dir)
    on_stack = rev_range is None and progress is None
    # On the branch checked out, `ger push` adds the missing Change-Ids: they hold nothing back.
    adds = change_ids_push_adds(view.rows) if on_stack and run.worktree.branch else frozenset[str]()
    blocked = (
        blocked_shas_for_stack(
            run.repo.root,
            project=run.repo.shown_stack.project,
            commits=view.rows,
            stop_pattern=settings.stop_pattern,
            settings=settings,
            web_base=settings.gerrit_web_url,
            change_ids_added=bool(adds),
        )
        if view.rows
        else frozenset[str]()
    )
    branch, whole = run.repo.working_branch, on_stack and run.worktree.branch is not None
    merged = merged_same(view.commits)
    moved = upstream_moved(run.cwd, branch, view.rows, settings=settings, merged=merged) if on_stack else None
    return StackOverview(view, blocked, progress, moved, whole_stack=whole, adds_change_id=adds - blocked)
