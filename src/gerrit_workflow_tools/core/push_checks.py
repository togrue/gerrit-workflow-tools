"""What a push looks at before it runs. Does not print or prompt."""

from __future__ import annotations

import contextlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import NamedTuple

from gerrit_workflow_tools.core.change_id import parse_change_id_footer, validate_change_id_value
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.change_view import ChangeView
from gerrit_workflow_tools.core.gerrit.rest import (
    GerritApiError,
    GerritUnavailableError,
)
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.gerrit.stack_context import (
    ChangeResolutionError,
    build_triplet,
)
from gerrit_workflow_tools.core.gerrit_change_status import (
    PatchsetStatus,
    is_diverged,
    overlay_code_facts,
    patchset_status,
)
from gerrit_workflow_tools.core.git_run import GitError, UnknownRevisionError, git, git_out
from gerrit_workflow_tools.core.git_state import current_branch, head_is_linear_on_remote_gerrit_target
from gerrit_workflow_tools.core.push_targets import NextPatchset, next_patchsets
from gerrit_workflow_tools.core.push_unchanged import AbandonedChange, OutdatedChange, UnchangedChange
from gerrit_workflow_tools.core.ready_calc import ReadyResult, compute_ready
from gerrit_workflow_tools.core.rebase_next_step import FINISH
from gerrit_workflow_tools.core.rebase_state import AM_UNDERWAY, am_underway, rebase_underway
from gerrit_workflow_tools.core.reword import add_missing_change_ids
from gerrit_workflow_tools.core.run_context import RunContext
from gerrit_workflow_tools.core.stack import Commit, RewriteCommit, is_unsquashed_fixup


class PushBlocker(Enum):
    """What is underway in the worktree that no Gerrit push may run over, whatever the stack holds."""

    REBASE = "rebase"
    """The branch ref moves only when the rebase finishes: a push now sends the stack as it was before."""
    AM = "am"
    """The branch moves with each patch ``git am`` applies: a push now sends it half applied."""


@dataclass(frozen=True)
class PushBlocked:
    """Why a Gerrit push cannot run now: ``ger push`` refuses with *message*, ``ger serve`` says it beforehand."""

    by: PushBlocker
    message: str


def push_blocked(repo_dir: Path | None, branch: str | None) -> PushBlocked | None:
    """Why no Gerrit push of *branch* can run now, or ``None``: the one verdict ``ger push`` and ``ger serve`` share.

    *repo_dir* is the worktree's git dir (``run.worktree.repository_dir``).
    """
    if rebase_underway(repo_dir):
        name = f"'{branch}'" if branch else "the branch"
        return PushBlocked(
            PushBlocker.REBASE,
            f"a rebase is in progress, and {name} still points at the commits from before it; {FINISH}, then push",
        )
    if am_underway(repo_dir):
        return PushBlocked(PushBlocker.AM, f"{AM_UNDERWAY}, then push")
    return None


_REBASE_HINT = "Hint: run `ger rebase --onto-remote` to replay your commits on top of the latest target branch."


def resolve_push_branch(run: RunContext, branch_arg: str | None) -> str:
    """Return the local branch ``ger push`` operates on: *branch_arg*, else the run's working branch."""
    if branch_arg:
        try:
            git_out("rev-parse", "--verify", branch_arg, cwd=run.cwd)
        except GitError as exc:
            raise UnknownRevisionError(f"branch {branch_arg!r} not found") from exc
        return branch_arg
    branch = run.repo.working_branch
    if branch is None:
        raise GitError(
            "ger push requires a branch (detached HEAD with no local branch at this commit). "
            "Check out a branch first, or pass --branch."
        )
    return branch


def git_or_none(cwd: Path, *args: str) -> str | None:
    try:
        return git_out(*args, cwd=cwd)
    except GitError:
        return None


def rebase_check(
    cwd: Path,
    branch: str,
    *,
    policy: str,
    no_rebase_check: bool,
    settings: Settings,
) -> tuple[str | None, tuple[str, ...]]:
    if no_rebase_check or policy == "ignore-not-rebased":
        return None, ()
    remote_name = settings.gerrit_remote
    try:
        git("fetch", remote_name, cwd=cwd)
    except GitError as exc:
        return None, (f"warning: could not fetch `{remote_name}`; skipping remote rebase check: {exc}",)
    try:
        ok, onto = head_is_linear_on_remote_gerrit_target(cwd, branch, head=branch, settings=settings)
    except GitError as exc:
        return None, (f"warning: could not compare HEAD to remote target; skipping remote rebase check: {exc}",)
    if ok:
        return None, ()
    message = (
        "Local HEAD is not based directly on the current Gerrit target branch after fetch "
        f"({_rebase_detail(cwd, branch, onto)}). {_REBASE_HINT}"
    )
    if policy == "error-not-rebased":
        return f"error: {message}", ()
    return None, (f"warning: {message}",)


def _rebase_detail(cwd: Path, head: str, onto: str) -> str:
    short_onto = (
        git_or_none(cwd, "rev-parse", "--abbrev-ref", onto) or git_or_none(cwd, "rev-parse", "--short", onto) or onto
    )
    tip = git_or_none(cwd, "rev-parse", "--short", onto)
    fork = git_or_none(cwd, "merge-base", head, onto)
    fork_s = git_or_none(cwd, "rev-parse", "--short", fork) if fork else None
    if tip and fork_s:
        return (
            f"tip of `{short_onto}` ({tip}) is not an ancestor of HEAD "
            f"(fork at {fork_s}; rebase onto the fetched target to linearize)"
        )
    return f"tip of `{short_onto}` is not an ancestor of HEAD"


class DivergedChange(NamedTuple):
    """A commit whose change's current patchset this repository never had (CONTEXT.md: **diverged**)."""

    short_sha: str
    number: int | None
    patch_set: int | None

    def describe(self) -> str:
        return (
            f"{self.short_sha} would replace patchset {self.patch_set} of change {self.number}, "
            "which was uploaded from elsewhere and is not in your history"
        )


class BuiltOnOutdated(NamedTuple):
    """A commit a push sends above an outdated one: Gerrit would chain it on a patchset that is not current."""

    short_sha: str
    below: OutdatedChange

    def describe(self) -> str:
        return (
            f"{self.short_sha} would be built on patchset {self.below.patch_set} of change {self.below.number}; "
            f"Gerrit has patchset {self.below.current_patch_set}"
        )


class ExistingChanges(NamedTuple):
    """What Gerrit already holds for the commits about to be pushed."""

    warnings: tuple[str, ...] = ()
    unchanged: tuple[UnchangedChange, ...] = ()
    """Commits that already are their change's current patchset, with that change's state."""
    drafts: tuple[tuple[str, int], ...] = ()
    """``(short SHA, count)`` of your unpublished drafts, for commits whose change has any."""
    gerrit_unavailable: bool = False
    """Gerrit could not be asked at all; :attr:`warnings` says why. Nothing else here is known."""
    diverged: tuple[DivergedChange, ...] = ()
    """Commits whose push would replace a patchset not built on: somebody else's upload."""
    outdated: tuple[OutdatedChange, ...] = ()
    """Commits Gerrit already has as an older patchset of their change: a push sends them nowhere."""
    built_on_outdated: tuple[BuiltOnOutdated, ...] = ()
    """The lowest sent commit above each outdated one: its patch set would not sit on Gerrit's current one."""
    reviewers: frozenset[str] = frozenset()
    """Who already reviews one of these changes: accounts Gerrit has, known without asking."""
    abandoned: tuple[AbandonedChange, ...] = ()
    """Commits Gerrit has as a patch set of an abandoned change: a push sends them nowhere."""
    next_patchsets: Mapping[str, NextPatchset] | None = None
    """By SHA, the commits that become new patch sets of a change Gerrit has; the rest become new changes.
    ``None``: Gerrit was not asked, so nobody knows."""

    def refusal(self) -> str | None:
        """Why a push without ``--force`` must not run: it would replace a newer patchset, or build on an old one."""
        lines: list[str] = []
        if self.diverged:
            lines.append(
                f"error: {'; '.join(d.describe() for d in self.diverged)}. Merge it into yours first "
                f"(`ger checkout change:{self.diverged[0].number} --update`), or replace it anyway with --force"
            )
        if self.built_on_outdated:
            lines.append(
                f"error: {'; '.join(b.describe() for b in self.built_on_outdated)}. Take it first "
                f"({self.built_on_outdated[0].below.take_it}), or push anyway with --force"
            )
        return "\n".join(lines) or None


def check_existing_changes(run: RunContext, commits: Sequence[Commit]) -> ExistingChanges:
    """Warn about abandoned changes and list the commits Gerrit will not see as new.

    Best effort: a push works without REST, so no credentials or a failed lookup finds nothing.
    """
    service = run.gerrit
    if not service.credentials_configured:
        return ExistingChanges()
    try:
        stack = run.stack
        triplets = {c.sha: build_triplet(stack.project, stack.push_branch, c.change_id) for c in commits if c.change_id}
        payloads = service.changes.get_payloads(list(triplets.values()))
    except GerritUnavailableError as error:
        return ExistingChanges(
            warnings=(f"warning: {error}; the preview shows no Gerrit state",), gerrit_unavailable=True
        )
    except (ValueError, GerritApiError, ChangeResolutionError):
        return ExistingChanges()
    warnings: list[str] = []
    unchanged: list[UnchangedChange] = []
    diverged: list[DivergedChange] = []
    outdated: list[OutdatedChange] = []
    abandoned: list[AbandonedChange] = []
    built_on_outdated: list[BuiltOnOutdated] = []
    views = {sha: ChangeView.from_payload(p) for sha, p in ((s, payloads.get(t)) for s, t in triplets.items()) if p}
    drafts = _draft_counts(service, commits, views)
    below: list[OutdatedChange] = []  # outdated, with no sent commit above yet
    facts = overlay_code_facts(run.cwd, [(c, views.get(c.sha)) for c in commits])
    for commit in commits:
        view = views.get(commit.sha)
        status = patchset_status(commit.sha, view) if view is not None else None
        if view is not None and (view.status or "").upper() == "ABANDONED":
            if status != PatchsetStatus.NEWER:  # Gerrit has it already: one of the change's patch sets
                abandoned.append(AbandonedChange(commit, view.number))
                continue
            warnings.append(
                f"warning: {commit.short_sha} would be a new patch set of abandoned change {view.number}, which "
                f"Gerrit refuses (restore it in the web UI, or `ger edit --drop {commit.short_sha}`)"
            )
        elif view is not None and status == PatchsetStatus.ACTIVE:
            unchanged.append(UnchangedChange.of(commit, triplets[commit.sha], view))
            continue
        elif view is not None and status == PatchsetStatus.OUTDATED:  # Gerrit has it; its newer patchset stays
            outdated.append(OutdatedChange.of(commit, view))
            below.append(outdated[-1])
            continue
        elif (
            view is not None
            and status == PatchsetStatus.NEWER
            and view.is_open
            and is_diverged(commit.sha, view, facts)
        ):
            diverged.append(DivergedChange(commit.short_sha, view.number, view.current_revision_number))
        built_on_outdated += (BuiltOnOutdated(commit.short_sha, change) for change in below)
        below.clear()
    return ExistingChanges(
        tuple(warnings),
        tuple(unchanged),
        drafts,
        diverged=tuple(diverged),
        outdated=tuple(outdated),
        built_on_outdated=tuple(built_on_outdated),
        reviewers=frozenset(account.slug for view in views.values() for account in view.reviewers or ()),
        abandoned=tuple(abandoned),
        next_patchsets=next_patchsets(commits, views, facts, user=run.settings.get("ger.user")),
    )


def _draft_counts(
    service: GerritService, commits: Sequence[Commit], views: dict[str, ChangeView]
) -> tuple[tuple[str, int], ...]:
    """Your drafts per commit (``CommentApi.draft_counts``). Best effort, like the rest of this
    check: a failed lookup finds no drafts."""
    numbers = {sha: view.number for sha, view in views.items() if view.number is not None}
    try:
        counts = service.comments.draft_counts(list(numbers.values()))
    except GerritApiError:
        return ()
    return tuple((c.short_sha, counts[n]) for c in commits if (n := numbers.get(c.sha)) is not None and n in counts)


@dataclass(frozen=True)
class PushBoundary:
    """Where the ready boundary stops a push, why, and what gets past it."""

    sha: str | None
    reason: str
    short_sha: str | None
    subject: str | None
    held_back: int
    """The commits from the boundary up, which this push leaves as they are (Gerrit may have them already)."""
    fix: str | None = None
    """What gets them past it: ``--all`` for the stop pattern or a ready strategy, else a Change-Id fix."""
    all_lifts: bool = False
    """``--all`` pushes past it (:attr:`ReadyResult.all_lifts_boundary`)."""
    missing_change_id: bool = False
    """It is a commit with no Change-Id at all: ``ger push`` adds the missing ones (:func:`add_change_ids_for_push`)."""

    def beyond(self, until: str | None) -> str:
        """``: it is above …``: why a commit of the local stack is not among those a push sends."""
        if until:
            return f": it is above {until}, where this push stops"
        if not self.sha:
            return ""
        way = "; add --all to push past it" if self.all_lifts else ""
        return f": it is above the ready boundary ({self.short_sha}: {self.reason}){way}"


def push_ready(
    run: RunContext,
    branch: str,
    *,
    all_commits: bool,
    until: str | None,
    until_sha: str | None,
    first_parent: bool,
) -> ReadyResult:
    """What of *branch* is below its ready boundary, as ``ger push`` finds it (stop pattern, strategy, Change-Ids)."""
    cwd, settings = run.cwd, run.settings
    project = settings.gerrit_project or ""
    with contextlib.suppress(Exception):
        project = run.stack.project
    return compute_ready(
        cwd,
        branch=branch,
        head=branch,
        all_commits=all_commits,
        until=until,
        until_sha=until_sha,
        first_parent=first_parent,
        stop_pattern=settings.stop_pattern,
        project=project,
        settings=settings,
        web_base=settings.gerrit_web_url,
    )


def push_boundary(cwd: Path, ready: ReadyResult, *, head: str) -> PushBoundary:
    """Where *ready* stops, and how many commits stay behind there."""
    sha = ready.boundary_sha
    short_sha = git_or_none(cwd, "rev-parse", "--short", sha) if sha else None
    subject = git_or_none(cwd, "show", "-s", "--format=%s", sha) if sha else None
    count = git_or_none(cwd, "rev-list", "--count", f"{sha}..{head}") if sha else None
    # The boundary commit itself is held back too, and `sha..head` leaves it out.
    held = int(count) + 1 if count and count.isdigit() else 0
    missing = sha is not None and not ready.all_lifts_boundary and _lacks_change_id(cwd, sha)
    fix = _boundary_fix(subject or "", ready, missing=missing) if sha else None
    return PushBoundary(sha, ready.boundary_reason, short_sha, subject, held, fix, ready.all_lifts_boundary, missing)


def _lacks_change_id(cwd: Path, sha: str) -> bool:
    """Whether *sha*'s message has no Change-Id footer at all (not a malformed one)."""
    message = git_or_none(cwd, "show", "-s", "--format=%B", sha) or ""
    found, malformed = validate_change_id_value(parse_change_id_footer(message))
    return not found and not malformed


def _boundary_fix(subject: str, ready: ReadyResult, *, missing: bool) -> str | None:
    """How the commits *ready* holds back get past its boundary: a Change-Id problem is fixed by
    ``ger change-id --fix`` (no commit-msg hook needed), which ``ger push`` runs itself for a missing one."""
    if ready.all_lifts_boundary:
        return "`ger push --all` sends them as work-in-progress"
    if is_unsquashed_fixup(subject):  # its reason says what to do already
        return None
    if missing:
        return "`ger change-id --fix` adds one (ger push does, on the branch checked out here)"
    return "`ger change-id --fix` gives it a new one"


def add_change_ids_for_push(cwd: Path, branch: str, boundary: PushBoundary | None) -> list[RewriteCommit]:
    """A push held back at a commit without a Change-Id: give every commit of the checked-out *branch*
    that lacks one a Change-Id (:func:`add_missing_change_ids`, as ``ger change-id --fix``), so it can
    go. Nothing for another reason, or another branch than the one checked out; the commits that got
    one otherwise."""
    if boundary is None or not boundary.missing_change_id or current_branch(cwd) != branch:
        return []
    return add_missing_change_ids(cwd, "HEAD")
