"""Core resolution helpers for `ger show`."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from gerrit_workflow_tools.core.annotated_targets import annotate_targets
from gerrit_workflow_tools.core.attention import Attention, as_your_review
from gerrit_workflow_tools.core.changeish import unresolved_change_message, unresolved_rev_message
from gerrit_workflow_tools.core.ci_content import CiContent
from gerrit_workflow_tools.core.ci_content_service import CiContentService
from gerrit_workflow_tools.core.comment_chains import CommentSelection, merge_chains
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.change_resolution import (
    ChangeResolutionError,
    Resolution,
    resolve_changeish,
)
from gerrit_workflow_tools.core.gerrit.change_view import ChangeView
from gerrit_workflow_tools.core.gerrit.changes import ChangeApi
from gerrit_workflow_tools.core.gerrit.rest import GerritApiError, GerritRest, GerritUnavailableError, norm_change_id
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.gerrit_change_status import CommentChain, LogCommit, PatchsetStatus
from gerrit_workflow_tools.core.git_run import commit_sha_or_none
from gerrit_workflow_tools.core.inbox import review_turns, to_review_needs_you_as_reviewer
from gerrit_workflow_tools.core.local_comments import local_chains_of
from gerrit_workflow_tools.core.stack import (
    Commit,
    CommitMeta,
    StackSnapshot,
    abbreviated_sha,
    commits_in_range,
    get_stack_snapshot,
    read_commit_rows,
)


@dataclass(frozen=True)
class ShowCommitResolution:
    """Resolved commit row for status lookup plus resolution metadata."""

    row: Commit
    is_local_commit: bool
    resolution: Resolution
    gerrit_meta: CommitMeta | None = None
    """A change with no local commit: its author, date and message as Gerrit has them, when it sent them."""

    @property
    def shown_triplet(self) -> str | None:
        """The change a target that is not a local commit is shown as; ``None``: the push branch's.

        A local commit is overlaid with the change it pushes to. Anything else is the selected
        change itself, which may be a **sibling** on another branch.
        """
        if self.is_local_commit or self.resolution.selected is None:
            return None
        return self.resolution.selected.triplet


def gerrit_rest_key(commit: LogCommit, resolution: object, service: GerritService) -> str | None:
    """Unambiguous REST key for *commit*: selected triplet, else push-branch triplet from Change-Id.

    Bare Change-Ids 404 on follow-up endpoints when the same id exists on multiple branches
    (Gerrit: "Multiple changes found"). Stack paths (``--stack``, git ranges) often have no
    ``resolution.selected``; narrow through :meth:`GerritService.cache_key` like reply does.
    """
    selected = getattr(resolution, "selected", None)
    if selected is not None:
        return selected.triplet
    if not commit.change_id:
        return None
    return service.cache_key(commit.change_id)


def _row_from_gerrit_change(change: ChangeView) -> Commit:
    """A **commit row** for a change with no local commit: Gerrit's current revision."""
    sha = change.current_revision or ""
    change_id = change.change_id
    if not change_id:
        raise ChangeResolutionError("Gerrit change has no change_id")
    summary = change.subject
    short = abbreviated_sha(sha) or "???????"
    meta = gerrit_commit_meta(change)
    return Commit(sha=sha, short_sha=short, subject=summary, body=meta.body if meta else "", change_id=change_id)


def gerrit_commit_meta(change: ChangeView) -> CommitMeta | None:
    """The current patchset's author, date and whole message as Gerrit has them (``CURRENT_COMMIT``);
    ``None`` when Gerrit did not send the commit."""
    revision = change.revisions.get(change.current_revision or "")
    if revision is None or revision.message is None:
        return None
    name, email, date = revision.author or ("", None, None)
    when = date.isoformat() if date else ""
    return CommitMeta(author_line=f"{name} <{email}>" if email else name, date=when, body=revision.message.rstrip("\n"))


def _row_from_local_commit(cwd: Path | str, sha: str) -> Commit:
    return read_commit_rows(cwd, "-1", sha)[0]


def resolve_show_commit_row(
    cwd: Path | str,
    arg: str | None,
    client: GerritRest,
    *,
    settings: Settings,
    branch: str | None = None,
    changes: ChangeApi,
) -> ShowCommitResolution:
    """Resolve one `ger show` argument via the shared changeish core.

    *arg* names a single revision (not a ``A..B`` range).
    """

    raw_arg = (arg or "HEAD").strip()
    resolution = resolve_changeish(
        raw_arg, client=client, cwd=cwd, settings=settings, branch=branch, explicit_target=True, changes=changes
    )

    # A change with a commit on the local stack renders as that commit, however it was named;
    # a sibling on another branch is not that commit's change (resolve_changeish decides).
    if resolution.local_sha is not None:
        row = _row_from_local_commit(cwd, resolution.local_sha)
        return ShowCommitResolution(row=row, is_local_commit=True, resolution=resolution)
    if resolution.kind == "git-rev":
        raise ChangeResolutionError(unresolved_rev_message(raw_arg))
    if resolution.selected is None:
        raise ChangeResolutionError(unresolved_change_message(raw_arg))

    change = ChangeView.from_payload(changes.get(resolution.selected.triplet))
    row = _row_from_gerrit_change(change)
    return ShowCommitResolution(row, False, resolution, gerrit_commit_meta(change))


def resolve_range_endpoint_sha(
    cwd: Path | str,
    arg: str,
    client: GerritRest,
    *,
    settings: Settings,
    branch: str | None = None,
    changes: ChangeApi,
) -> str:
    """Resolve a range endpoint changeish to a local commit SHA."""
    raw = arg.strip()
    if not raw:
        raise ChangeResolutionError("empty range endpoint")
    resolution = resolve_changeish(
        raw, client=client, cwd=cwd, settings=settings, branch=branch, explicit_target=True, changes=changes
    )
    if resolution.local_sha:
        return resolution.local_sha

    change_id: str | None = None
    if resolution.selected is not None:
        change_id = resolution.selected.change_id
    elif resolution.kind == "change-id":
        from gerrit_workflow_tools.core.changeish import parse

        parsed = parse(raw)
        change_id = parsed.change_id

    if change_id:
        for commit in get_stack_snapshot(cwd, branch, settings=settings).carrying(change_id):
            return commit.sha
        if resolution.selected is not None:
            rev = ChangeView.from_payload(changes.get(resolution.selected.triplet)).current_revision
            if rev is not None:
                local = commit_sha_or_none(cwd, rev)
                if local is not None:
                    return local

    raise ChangeResolutionError(f"range endpoint {raw!r} does not resolve to a local commit")


def parse_show_range(arg: str) -> tuple[str, str, str] | None:
    """If *arg* is ``A..B`` or ``A...B``, return ``(left, dots, right)``; else ``None``.

    Empty right defaults to ``HEAD``. Empty left is an error.
    """
    s = arg.strip()
    if "..." in s:
        left, right = s.split("...", 1)
        dots = "..."
    elif ".." in s:
        left, right = s.split("..", 1)
        dots = ".."
    else:
        return None
    left, right = left.strip(), right.strip()
    if not left:
        raise ChangeResolutionError(f"invalid revision range: {arg!r}")
    if not right:
        right = "HEAD"
    return left, dots, right


def _local_resolutions(rows: Iterable[Commit], *, input_arg: str) -> list[ShowCommitResolution]:
    return [
        ShowCommitResolution(
            row=row,
            is_local_commit=True,
            resolution=Resolution(input=input_arg, kind="git-rev", local_sha=row.sha),
        )
        for row in rows
    ]


def expand_show_range(
    cwd: Path | str,
    arg: str,
    client: GerritRest,
    *,
    settings: Settings,
    branch: str | None = None,
    changes: ChangeApi,
) -> list[ShowCommitResolution]:
    """Expand a changeish-resolved ``A..B`` / ``A...B`` range to local commits (oldest first)."""
    parsed = parse_show_range(arg)
    if parsed is None:
        raise ChangeResolutionError(f"not a revision range: {arg!r}")
    left, dots, right = parsed
    left_sha = resolve_range_endpoint_sha(cwd, left, client, settings=settings, branch=branch, changes=changes)
    right_sha = resolve_range_endpoint_sha(cwd, right, client, settings=settings, branch=branch, changes=changes)
    rev_range = f"{left_sha}{dots}{right_sha}"
    return _local_resolutions(commits_in_range(cwd, rev_range), input_arg=arg)


def expand_stack_range(
    cwd: Path | str,
    *,
    branch: str | None = None,
    settings: Settings | None = None,
    head: str = "HEAD",
) -> list[ShowCommitResolution]:
    """Oldest-first commits in ``upstream_tip..head``."""
    snap = get_stack_snapshot(cwd, branch, head=head, settings=settings)
    return _local_resolutions(snap.commits, input_arg="--stack")


def _dedupe_key(resolved: ShowCommitResolution) -> str:
    cid = resolved.row.valid_change_id
    if cid:
        return f"cid:{norm_change_id(cid)}"
    sha = resolved.row.sha
    if sha:
        return f"sha:{sha}"
    return f"id:{id(resolved)}"


def resolve_show_targets(
    cwd: Path | str,
    args: list[str],
    client: GerritRest,
    *,
    settings: Settings,
    stack: bool = False,
    branch: str | None = None,
    changes: ChangeApi,
    head: str = "HEAD",
) -> list[ShowCommitResolution]:
    """Resolve positional changeishes / ranges and optional ``--stack`` into a deduped list.

    With no *args* and ``stack=False``, resolves *head*: ``HEAD``, or what it stands for
    (``run.repo.head``), which also tops the ``--stack``. Gerrit is asked through *changes* (the
    service's :class:`ChangeApi`), so through the cache. Order: ``--stack`` commits first
    (oldest-first), then each positional arg in order (ranges oldest-first). Duplicates
    (same Change-Id, else same SHA) keep the first occurrence.
    """
    pieces: list[ShowCommitResolution] = []
    if stack:
        pieces.extend(expand_stack_range(cwd, branch=branch, settings=settings, head=head))
    if not args and not stack:
        pieces.append(resolve_show_commit_row(cwd, head, client, settings=settings, branch=branch, changes=changes))
    for raw in args:
        if parse_show_range(raw) is not None:
            pieces.extend(expand_show_range(cwd, raw, client, settings=settings, branch=branch, changes=changes))
        else:
            pieces.append(resolve_show_commit_row(cwd, raw, client, settings=settings, branch=branch, changes=changes))

    first_by_key: dict[str, ShowCommitResolution] = {}
    for item in pieces:
        first_by_key.setdefault(_dedupe_key(item), item)
    return list(first_by_key.values())


@dataclass(frozen=True)
class ShownCommit:
    """One resolved target with everything ``ger show`` reports about it."""

    target: ShowCommitResolution
    commit: LogCommit
    chains: list[CommentChain]
    """Gerrit and local comment chains, as selected."""
    ci_contents: list[CiContent] | None
    """**CI content** per failed **CI link**; ``None`` when it was not asked for."""
    gerrit_unavailable: GerritUnavailableError | None = None
    """Why Gerrit could not be asked; :attr:`commit` then holds local state only."""
    blocked_by: LogCommit | None = None
    """The commit of the local stack that keeps this one from being submitted, when one does."""

    @property
    def ci_verdict_contents(self) -> list[CiContent] | None:
        """:attr:`ci_contents` when Gerrit's CI verdict is about this very commit, else ``None``.

        A commit that is not on Gerrit, or is ahead of the patchset CI ran on, has no known CI state.
        """
        if not self.commit.pushed or self.commit.patchset_status == PatchsetStatus.NEWER:
            return None
        return self.ci_contents


def _mark_waiting_on_you(service: GerritService, commits: list[LogCommit]) -> None:
    """Of *commits*, each you review leads with whose turn it is (:func:`as_your_review`).

    Commits of the local stack are asked too: a change checked out for review (``ger review``), or
    a colleague's change your work sits on, is in the stack and still waits on you. The query's own
    ``-owner:self`` leaves your changes out. Gerrit not answering leaves the author's reasons.

    With the built-in query only a change that lists you as a reviewer can match (it asks
    ``reviewer:self``), so the query is asked for those alone, and not at all when none does:
    showing your own stack costs no request. Who you are is asked once per user and host. A custom
    ``ger.inboxToReviewQuery`` may select changes any other way: every shown change is asked.
    """
    pushed = [c for c in commits if c.pushed and c.number is not None]
    if to_review_needs_you_as_reviewer(service.settings):
        pushed = [c for c in pushed if any(not r.cc for r in c.reviewers)]
        me = service.self_account_id() if pushed else None
        if me is None:
            return
        pushed = [c for c in pushed if any(r.account_id == me and not r.cc for r in c.reviewers)]
    numbers = [c.number for c in pushed if c.number is not None]
    try:
        turns = review_turns(service, numbers)
    except GerritApiError:
        return
    for commit in commits:
        if commit.number in turns:
            commit.attention_reasons = as_your_review(commit.attention_reasons, your_turn=turns[commit.number])


def load_shown_commits(
    targets: list[ShowCommitResolution],
    *,
    service: GerritService,
    comments: CommentSelection = "unresolved",
    context_padding: int | None = None,
    fetch_ci_pipelines: bool = False,
    ci: CiContentService | None = None,
    stack: StackSnapshot | None = None,
) -> list[ShownCommit]:
    """Overlay, attention, comment chains and (with *ci*) **CI content** for *targets*, in order.

    A target that waits on your review (the *to review* query matches it, in any project and with
    no limit) leads with that.

    A target in the local *stack* is judged within it, as ``ger log`` judges it
    (:func:`annotate_targets`). A CI link that cannot be fetched stays on its result; nothing
    here raises for it.
    """
    annotated = annotate_targets(
        [t.row for t in targets],
        service=service,
        stack=stack,
        local=[t.is_local_commit for t in targets],
        triplets=[t.shown_triplet for t in targets],
        fetch_ci_pipelines=fetch_ci_pipelines,
    )
    commits = annotated.commits
    if len(commits) != len(targets):
        raise ChangeResolutionError("commit annotation mismatch")
    if annotated.gerrit_unavailable is None:
        _mark_waiting_on_you(service, commits)
    per_commit: list[list[CiContent] | None] = [None] * len(commits)
    if ci is not None:
        per_commit = list(ci.fetch_each([(commit.ci_links, commit.sha or None) for commit in commits]))
    shown: list[ShownCommit] = []
    for target, commit, ci_contents in zip(targets, commits, per_commit, strict=True):
        rest_key = gerrit_rest_key(commit, target.resolution, service)
        chains: list[CommentChain] = []
        if commit.pushed and rest_key:
            chains = service.comments.get_chains(
                rest_key, change_updated=commit.freshness, context_padding=context_padding, comments=comments
            )
        if rest_key:
            chains = merge_chains(chains, local_chains_of(service, rest_key, commit.sha or None, comments=comments))
        blocked_by = annotated.blocker if Attention.CHAIN_BLOCKED in commit.attention_reasons else None
        shown.append(ShownCommit(target, commit, chains, ci_contents, annotated.gerrit_unavailable, blocked_by))
    return shown
