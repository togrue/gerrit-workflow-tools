"""Shared Gerrit changeish classification, stack context, and resolution."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from gerrit_workflow_tools.core.change_id import change_id_of_rev
from gerrit_workflow_tools.core.changeish import (
    KINDS_NEEDING_GERRIT,
    Changeish,
    ChangeishKind,
    address_problem,
    not_in_stack_message,
    parse,
    unresolved_change_message,
    unresolved_rev_message,
)
from gerrit_workflow_tools.core.config import ConfigError, Settings
from gerrit_workflow_tools.core.gerrit.change_view import ChangeView
from gerrit_workflow_tools.core.gerrit.http_rest import HttpGerritRest
from gerrit_workflow_tools.core.gerrit.narrowing import narrow_matches
from gerrit_workflow_tools.core.gerrit.resolution import (
    ChangeAmbiguousError,
    LocalSibling,
    Resolution,
    SelectedChange,
    SelectedReason,
    format_local_sibling_note,
    format_resolution_note,
    local_sibling_note,
)
from gerrit_workflow_tools.core.gerrit.rest import (
    GerritApiError,
    GerritRest,
    GerritUnavailableError,
    pick_change_from_query_result,
)
from gerrit_workflow_tools.core.gerrit.stack_context import (
    ChangeResolutionError,
    StackContext,
    resolve_stack_context,
)
from gerrit_workflow_tools.core.git_run import GitError, commit_sha_or_none
from gerrit_workflow_tools.core.stack import get_stack_snapshot


# The changeish grammar lives in core.changeish; this module decides what a parsed changeish
# *means*. ChangeishKind is re-exported because Resolution.kind is part of the JSON contract; the
# resolution model (core.gerrit.resolution) is re-exported so callers keep one import.
__all__ = [
    "ChangeAmbiguousError",
    "ChangeResolutionError",
    "ChangeishKind",
    "LocalSibling",
    "Resolution",
    "SelectedChange",
    "SelectedReason",
    "format_local_sibling_note",
    "format_resolution_note",
    "resolve_changeish",
    "resolve_stack_changeish",
    "resolve_to_stack_sha",
]


def parse_changeish(s: str) -> Changeish:
    """Parse *s*, rejecting blank input: the grammar itself is total."""
    parsed = parse(s)
    if not parsed.raw:
        raise ChangeResolutionError("empty changeish")
    return parsed


def local_commit_sha(cwd: Path | str | None, rev: str) -> str:
    """Full SHA of the commit *rev* names; the one "cannot resolve" error every command shares."""
    sha = commit_sha_or_none(cwd, rev)
    if sha is None:
        raise ChangeResolutionError(unresolved_rev_message(rev))
    return sha


def _fetch_change_row(client: GerritRest, change_key: str, raw: str) -> dict[str, Any]:
    """Only Gerrit's 404 means "not found"; any other failure is Gerrit's and says so."""
    try:
        return client.get_change(change_key)
    except GerritApiError as e:
        if e.status != 404:
            raise
        raise ChangeResolutionError(unresolved_change_message(raw)) from e


def _fetch_single_from_query(client: GerritRest, query: str) -> dict[str, Any]:
    rows = client.query_changes(query)
    try:
        return pick_change_from_query_result(rows)
    except GerritApiError as e:
        raise ChangeResolutionError(str(e)) from e


def _fetch_unique_row(parsed: Changeish, client: GerritRest, *, web_base: str | None) -> dict[str, Any]:
    """Fetch the one ChangeInfo a directly-addressed changeish names.

    Triplet, change number, ``refs/changes/…`` ref, URL and query all name exactly one change
    and differ only in how the address is spelled — which the grammar has already worked out.
    """
    if problem := address_problem(parsed, web_base=web_base):
        raise ChangeResolutionError(problem)
    if parsed.kind == "triplet":
        return _fetch_change_row(client, parsed.raw, parsed.raw)

    if parsed.kind == "query":
        if not parsed.query:
            raise ChangeResolutionError("empty Gerrit query")
        return _fetch_single_from_query(client, parsed.query)

    number = parsed.number
    if parsed.kind == "change-number" and not (number and number.isdigit() and int(number) > 0):
        raise ChangeResolutionError(f"invalid change number: {parsed.raw!r}")
    if number is None:
        raise ChangeResolutionError(f"cannot parse Gerrit change number from URL: {parsed.raw!r}")
    row = _fetch_change_row(client, number, parsed.raw)
    if problem := ChangeView.from_payload(row).contradicts(parsed):
        raise ChangeResolutionError(problem)
    return row


def resolve_changeish(
    ref: str,
    *,
    client: GerritRest,
    cwd: Path | str | None,
    settings: Settings,
    branch: str | None = None,
    explicit_target: bool = False,
) -> Resolution:
    """Resolve *ref* to a :class:`Resolution` using stack context and Gerrit.

    ``local_sha`` is the commit a git rev names, else the **local stack** commit carrying the
    selected change's Change-Id, however that change was addressed — provided that commit pushes
    to the selected change (same project and **push branch**). A commit carrying the Change-Id
    while pushing to another branch belongs to a **sibling**: it is ``local_sibling`` instead.
    Every command that reports a resolution takes it from here, so ``ger show`` and
    ``ger resolve`` cannot disagree on it.
    """
    resolution = _resolve_on_gerrit(
        ref, client=client, cwd=cwd, settings=settings, branch=branch, explicit_target=explicit_target
    )
    selected = resolution.selected
    if resolution.local_sha is not None or selected is None:
        return resolution
    sha = _local_stack_sha(cwd, selected.change_id, branch=branch, settings=settings)
    if sha is None:
        return resolution
    stack = _stack_context_or_none(cwd, branch, settings)
    if stack is None or (selected.project, selected.branch) == (stack.project, stack.push_branch):
        resolution.local_sha = sha
        return resolution
    on_push_branch = [alt for alt in resolution.alternatives if alt.branch == stack.push_branch]
    number = on_push_branch[0].number if len(on_push_branch) == 1 else None
    resolution.local_sibling = LocalSibling(sha=sha, branch=stack.push_branch, number=number)
    return resolution


def _stack_context_or_none(cwd: Path | str | None, branch: str | None, settings: Settings) -> StackContext | None:
    try:
        return resolve_stack_context(cwd, branch, settings=settings)
    except ChangeResolutionError:
        return None


def _local_stack_sha(cwd: Path | str | None, change_id: str, *, branch: str | None, settings: Settings) -> str | None:
    """The **local stack** commit carrying *change_id*; ``None`` without one, or without a stack."""
    try:
        matches = get_stack_snapshot(cwd, branch, settings=settings).carrying(change_id)
    except (GitError, ConfigError):
        return None
    return matches[0].sha if matches else None


def _resolve_on_gerrit(
    ref: str,
    *,
    client: GerritRest,
    cwd: Path | str | None,
    settings: Settings,
    branch: str | None,
    explicit_target: bool,
) -> Resolution:
    """:func:`resolve_changeish` before the local stack is searched for a Gerrit-side changeish."""
    parsed = parse_changeish(ref)
    resolution = Resolution(input=parsed.raw, kind=parsed.kind)

    # A git rev and a bare Change-Id both go through Change-Id narrowing; they differ only in
    # where the Change-Id comes from. Everything else addresses one change directly.
    change_id = parsed.change_id
    if parsed.kind == "git-rev":
        resolution.local_sha = local_commit_sha(cwd, parsed.rev or parsed.raw)
        change_id = change_id_of_rev(cwd, resolution.local_sha)
        if not change_id:
            return resolution

    if change_id is not None and parsed.kind in ("git-rev", "change-id"):
        try:
            rows = client.query_changes(f"change:{change_id}")
        except GerritUnavailableError:  # A local commit still shows; the overlay reports why.
            if parsed.kind != "git-rev":
                raise
            rows = []
        if not rows:
            return resolution
        try:
            stack = resolve_stack_context(cwd, branch, settings=settings)
        except ChangeResolutionError:
            if explicit_target and len(rows) == 1:
                return replace(resolution, selected=SelectedChange.from_change_row(rows[0]), selected_reason="unique")
            raise
        return narrow_matches(resolution, rows, push_branch=stack.push_branch, explicit_target=explicit_target)

    row = _fetch_unique_row(parsed, client, web_base=settings.gerrit_web_url or client.web_base)
    resolution.selected = SelectedChange.from_change_row(row)
    resolution.selected_reason = "unique"
    return resolution


@dataclass(frozen=True)
class StackCommit:
    """A changeish resolved to a commit on the **local stack**.

    *resolution* is present only when Gerrit was actually consulted to get there. A
    **Change-Id**, **triplet** or git rev is matched against the stack offline, so there is
    nothing for Gerrit to have narrowed and nothing to report.
    """

    sha: str
    resolution: Resolution | None = None
    note: str | None = None
    """Set when the changeish named a **sibling**: a change on another branch than the one this
    commit pushes to. The command still acts on this commit (spec §4), and says so."""


def _stack_commit(
    cwd: Path | str | None, branch: str | None, raw: str, sha: str, *, require_in_stack: bool
) -> StackCommit:
    """Wrap *sha*, which *raw* named, optionally insisting it is one of the **local stack**'s commits."""
    if require_in_stack and sha not in {c.sha for c in get_stack_snapshot(cwd, branch).commits}:
        raise ChangeResolutionError(not_in_stack_message(raw, sha))
    return StackCommit(sha=sha)


def resolve_to_stack_sha(
    ref: str,
    *,
    cwd: Path | str | None,
    settings: Settings,
    branch: str | None = None,
    client_factory: Callable[[], GerritRest] | None = None,
    require_in_stack: bool = False,
) -> StackCommit:
    """Resolve a changeish to a full SHA on the current local stack.

    Never fetches. Kinds that carry a Change-Id resolve without touching the network; kinds
    that address a change by number cost one Gerrit round trip to learn the id first.

    *client_factory* is called only on the paths that genuinely need Gerrit, so a caller can
    defer resolving ``gerrit.webUrl`` — a ``refs/changes/…`` ref the repository already has
    must not require Gerrit configuration to use.

    *require_in_stack* additionally insists the answer is a commit *in* the stack. Commands
    that rewrite a stack commit (``ger edit``, ``ger reword``, ``ger fix``) want that; the one
    that takes a commit to rebase *from* (``ger rebase``) does not, because its argument is
    normally below the stack.
    """
    parsed = parse_changeish(ref)

    if parsed.kind == "git-rev":
        sha = local_commit_sha(cwd, parsed.raw)
        return _stack_commit(cwd, branch, parsed.raw, sha, require_in_stack=require_in_stack)

    if parsed.kind == "change-ref":
        # A refs/changes ref you already have is just a git ref. Asking Gerrit to translate a
        # ref the repository can resolve on its own would be a round trip for nothing.
        local = commit_sha_or_none(cwd, parsed.raw)
        if local is not None:
            return _stack_commit(cwd, branch, parsed.raw, local, require_in_stack=require_in_stack)

    # A Change-Id or triplet already carries the id, so the stack can be searched offline.
    # The remaining kinds address a change by number, so Gerrit has to name the id first.
    change_id = parsed.change_id
    resolution: Resolution | None = None
    if change_id is None:
        if client_factory is None:
            raise ChangeResolutionError(f"cannot resolve {parsed.kind!r} to a stack commit without a Gerrit client")
        resolution = resolve_changeish(
            parsed.raw, client=client_factory(), cwd=cwd, settings=settings, branch=branch, explicit_target=True
        )
        if resolution.selected is None:
            raise ChangeResolutionError(unresolved_change_message(parsed.raw))
        change_id = resolution.selected.change_id

    # Searching the stack for the Change-Id already guarantees membership, so this path needs
    # no separate require_in_stack check.
    matches = get_stack_snapshot(cwd, branch).carrying(change_id)
    if not matches:
        raise ChangeResolutionError(not_in_stack_message(parsed.raw))
    if len(matches) > 1:
        shorts = [c.short_sha for c in matches]
        raise ChangeAmbiguousError(
            f"ambiguous Change-Id {change_id} in stack ({', '.join(shorts)})",
            alternatives=[],
        )
    sha = matches[0].sha
    if resolution is not None:
        return StackCommit(sha=sha, resolution=resolution, note=format_local_sibling_note(resolution))
    return StackCommit(sha=sha, note=_triplet_sibling_note(parsed, sha, cwd=cwd, branch=branch, settings=settings))


def _triplet_sibling_note(
    parsed: Changeish, sha: str, *, cwd: Path | str | None, branch: str | None, settings: Settings
) -> str | None:
    """The sibling note for a **triplet** naming another branch than the one *sha* pushes to. Offline."""
    if parsed.kind != "triplet" or parsed.branch is None:
        return None
    stack = _stack_context_or_none(cwd, branch, settings)
    if stack is None or (parsed.project, parsed.branch) == (stack.project, stack.push_branch):
        return None
    return local_sibling_note(LocalSibling(sha=sha, branch=stack.push_branch), branch=parsed.branch)


def resolve_stack_changeish(
    cwd: Path | str | None,
    ref: str,
    *,
    settings: Settings,
    gerrit: GerritRest | None = None,
    branch: str | None = None,
    require_in_stack: bool = False,
) -> StackCommit:
    """Resolve *ref* to a stack commit the way every stack-rewriting command should.

    The single entry point for ``ger fix``, ``ger edit``, ``ger reword`` and ``ger rebase``.

    Pass *gerrit* to supply an implementation; otherwise one is built from *settings* — but
    only on the paths that need it, so ``gerrit.webUrl`` stays irrelevant to a purely local
    resolution.
    """
    needs_gerrit = parse(ref).kind in KINDS_NEEDING_GERRIT

    def _build_client() -> GerritRest:
        if gerrit is not None:
            return gerrit
        return HttpGerritRest.from_settings(settings)

    return resolve_to_stack_sha(
        ref,
        cwd=cwd,
        settings=settings,
        branch=branch,
        client_factory=_build_client if needs_gerrit else None,
        require_in_stack=require_in_stack,
    )
