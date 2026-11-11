"""Shared Gerrit changeish classification, stack context, and resolution."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from gerrit_workflow_tools.core.change_id import change_id_of_rev
from gerrit_workflow_tools.core.changeish import (
    KINDS_NEEDING_GERRIT,
    Changeish,
    ChangeishKind,
    address_problem,
    parse,
    unresolved_rev_message,
)
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.change_view import ChangeView
from gerrit_workflow_tools.core.gerrit.http_rest import HttpGerritRest
from gerrit_workflow_tools.core.gerrit.rest import (
    GerritApiError,
    GerritRest,
    GerritUnavailableError,
    norm_change_id,
    pick_change_from_query_result,
)
from gerrit_workflow_tools.core.gerrit.stack_context import ChangeResolutionError, build_triplet, resolve_stack_context
from gerrit_workflow_tools.core.git_run import commit_sha_or_none
from gerrit_workflow_tools.core.stack import get_stack_snapshot


SelectedReason = Literal["unique", "target-branch", "prefer-open", "explicit", "branch-mismatch"]

_INACTIVE_STATUSES = frozenset({"ABANDONED", "MERGED"})

# The changeish grammar lives in core.changeish; this module decides what a parsed changeish
# *means*. ChangeishKind is re-exported because Resolution.kind is part of the JSON contract.
__all__ = [
    "ChangeAmbiguousError",
    "ChangeResolutionError",
    "ChangeishKind",
    "Resolution",
    "SelectedChange",
    "SelectedReason",
    "format_resolution_note",
    "resolution_from_change_rows",
    "resolve_changeish",
    "resolve_stack_changeish",
    "resolve_to_stack_sha",
]


class ChangeAmbiguousError(ChangeResolutionError):
    """Multiple Gerrit changes matched after narrowing."""

    def __init__(self, message: str, *, alternatives: list[SelectedChange]) -> None:
        listed = ", ".join(f"change:{alt.number} ({alt.triplet}, {alt.status})" for alt in alternatives)
        super().__init__(f"{message}: {listed}. Name one by its change: number or triplet" if listed else message)
        self.alternatives = alternatives


@dataclass(frozen=True)
class SelectedChange:
    """One resolved Gerrit change row."""

    number: int
    triplet: str
    branch: str
    change_id: str
    status: str

    @classmethod
    def from_change_row(cls, row: dict[str, Any]) -> SelectedChange:
        number = row.get("_number")
        triplet = row.get("id")
        branch = row.get("branch")
        change_id = row.get("change_id")
        status = row.get("status")
        project = row.get("project")
        if not isinstance(number, int):
            raise ChangeResolutionError("Gerrit change row missing _number")
        if not isinstance(triplet, str) or not triplet:
            raise ChangeResolutionError("Gerrit change row missing id (triplet)")
        if not isinstance(branch, str) or not branch:
            raise ChangeResolutionError("Gerrit change row missing branch")
        if not isinstance(change_id, str) or not change_id:
            raise ChangeResolutionError("Gerrit change row missing change_id")
        return cls(
            number=number,
            # Gerrit's `id` may be the compact project~number; a triplet is always the full form.
            triplet=build_triplet(project, branch, change_id) if isinstance(project, str) and project else triplet,
            branch=branch,
            change_id=change_id,
            status=status if isinstance(status, str) else "UNKNOWN",
        )

    def to_json_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Resolution:
    """Result of resolving a changeish (mirrors spec ``resolution`` JSON block)."""

    input: str
    kind: ChangeishKind
    selected: SelectedChange | None = None
    selected_reason: SelectedReason | None = None
    ambiguous: bool = False
    alternatives: list[SelectedChange] = field(default_factory=list)
    local_sha: str | None = None

    def to_json_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "input": self.input,
            "kind": self.kind,
            "ambiguous": self.ambiguous,
            "alternatives": [alt.to_json_dict() for alt in self.alternatives],
        }
        if self.selected is not None:
            out["selected"] = self.selected.to_json_dict()
        if self.selected_reason is not None:
            out["selected_reason"] = self.selected_reason
        if self.local_sha is not None:
            out["local_sha"] = self.local_sha
        return out


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


def _fetch_change_row(client: GerritRest, change_key: str) -> dict[str, Any]:
    """Only Gerrit's 404 means "not found"; any other failure is Gerrit's and says so."""
    try:
        return client.get_change(change_key)
    except GerritApiError as e:
        if e.status != 404:
            raise
        raise ChangeResolutionError(f"Gerrit change not found: {change_key!r}") from e


def _fetch_single_from_query(client: GerritRest, query: str) -> dict[str, Any]:
    rows = client.query_changes(query)
    try:
        return pick_change_from_query_result(rows)
    except GerritApiError as e:
        raise ChangeResolutionError(str(e)) from e


def _is_active_status(status: str) -> bool:
    return status.upper() not in _INACTIVE_STATUSES


def _narrow_matches(
    resolution: Resolution,
    rows: list[dict[str, Any]],
    *,
    push_branch: str,
    explicit_target: bool,
) -> Resolution:
    """*resolution* with spec §3.1 narrowing applied to one Change-Id's rows. Never contacts Gerrit."""
    changes = [SelectedChange.from_change_row(row) for row in rows]
    on_branch = [change for change in changes if change.branch == push_branch]
    elsewhere = [change for change in changes if change.branch != push_branch]

    def _picked(selected: SelectedChange, reason: SelectedReason, alternatives: list[SelectedChange]) -> Resolution:
        return replace(
            resolution,
            selected=selected,
            selected_reason=reason,
            ambiguous=reason != "unique",
            alternatives=alternatives,
        )

    if len(changes) == 1 and on_branch:
        return _picked(changes[0], "unique", [])
    if len(on_branch) == 1:
        return _picked(on_branch[0], "target-branch", elsewhere)
    if on_branch:
        active = [change for change in on_branch if _is_active_status(change.status)]
        if len(active) == 1:
            inactive = [change for change in on_branch if change is not active[0]]
            return _picked(active[0], "prefer-open", elsewhere + inactive)
        matched = f"{len(active)} open changes" if active else f"{len(on_branch)} changes"
        raise ChangeAmbiguousError(
            f"ambiguous Change-Id: {matched} on branch {push_branch!r}",
            alternatives=active or on_branch,
        )
    if not explicit_target or not changes:
        return replace(resolution, ambiguous=len(changes) > 1, alternatives=changes)
    if len(changes) == 1:
        return _picked(changes[0], "branch-mismatch", [])
    raise ChangeAmbiguousError(
        f"ambiguous Change-Id: matches changes on other branches (not {push_branch!r})",
        alternatives=changes,
    )


def resolution_from_change_rows(
    change_id: str,
    rows: list[dict[str, Any]],
    *,
    push_branch: str,
    explicit_target: bool = False,
) -> Resolution:
    """Build a :class:`Resolution` from already-fetched/cached ChangeInfo rows."""
    return _narrow_matches(
        Resolution(input=change_id, kind="change-id"), rows, push_branch=push_branch, explicit_target=explicit_target
    )


def _fetch_unique_row(parsed: Changeish, client: GerritRest, *, web_base: str | None) -> dict[str, Any]:
    """Fetch the one ChangeInfo a directly-addressed changeish names.

    Triplet, change number, ``refs/changes/…`` ref, URL and query all name exactly one change
    and differ only in how the address is spelled — which the grammar has already worked out.
    """
    if problem := address_problem(parsed, web_base=web_base):
        raise ChangeResolutionError(problem)
    if parsed.kind == "triplet":
        return _fetch_change_row(client, parsed.raw)

    if parsed.kind == "query":
        if not parsed.query:
            raise ChangeResolutionError("empty Gerrit query")
        return _fetch_single_from_query(client, parsed.query)

    number = parsed.number
    if parsed.kind == "change-number" and not (number and number.isdigit() and int(number) > 0):
        raise ChangeResolutionError(f"invalid change number: {parsed.raw!r}")
    if number is None:
        raise ChangeResolutionError(f"cannot parse Gerrit change number from URL: {parsed.raw!r}")
    row = _fetch_change_row(client, number)
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
    """Resolve *ref* to a :class:`Resolution` using stack context and Gerrit."""
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
        return _narrow_matches(resolution, rows, push_branch=stack.push_branch, explicit_target=explicit_target)

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


def _stack_commit(
    cwd: Path | str | None,
    branch: str | None,
    sha: str,
    *,
    require_in_stack: bool,
    resolution: Resolution | None = None,
) -> StackCommit:
    """Wrap *sha*, optionally insisting it is one of the **local stack**'s commits."""
    if require_in_stack:
        snap = get_stack_snapshot(cwd, branch)
        if sha not in {c.sha for c in snap.commits}:
            raise ChangeResolutionError(f"commit {sha[:8]} is not in the current local stack")
    return StackCommit(sha=sha, resolution=resolution)


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
        return _stack_commit(cwd, branch, sha, require_in_stack=require_in_stack)

    if parsed.kind == "change-ref":
        # A refs/changes ref you already have is just a git ref. Asking Gerrit to translate a
        # ref the repository can resolve on its own would be a round trip for nothing.
        local = commit_sha_or_none(cwd, parsed.raw)
        if local is not None:
            return _stack_commit(cwd, branch, local, require_in_stack=require_in_stack)

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
            raise ChangeResolutionError(f"no Gerrit change resolved for {parsed.raw!r}")
        change_id = resolution.selected.change_id

    # Searching the stack for the Change-Id already guarantees membership, so this path needs
    # no separate require_in_stack check.
    snap = get_stack_snapshot(cwd, branch)
    # Case-insensitively, because the grammar accepts either case: `ger edit I5F3A…` has to
    # find a commit whose footer spells the same id in lowercase.
    want = norm_change_id(change_id)
    matches = [c for c in snap.commits if c.change_id and norm_change_id(c.change_id) == want]
    if not matches:
        raise ChangeResolutionError(f"no commit in current stack with Change-Id {change_id}")
    if len(matches) > 1:
        shorts = [c.short_sha for c in matches]
        raise ChangeAmbiguousError(
            f"ambiguous Change-Id {change_id} in stack ({', '.join(shorts)})",
            alternatives=[],
        )
    return StackCommit(sha=matches[0].sha, resolution=resolution)


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


def format_resolution_note(resolution: Resolution) -> str | None:
    """Return a one-line stderr transparency note when narrowing occurred."""
    if not resolution.ambiguous or resolution.selected is None:
        if resolution.selected is None and resolution.alternatives:
            alts = resolution.alternatives
            branches = ", ".join(f"{alt.branch} #{alt.number}" for alt in alts)
            return (
                f"note: Change-Id matches {len(alts)} change(s) on other branch(es) "
                f"({branches}); absent on your push target."
            )
        return None

    selected = resolution.selected
    all_matches = [selected, *resolution.alternatives]
    total = len(all_matches)
    branches = ", ".join(f"{alt.branch} #{alt.number}" for alt in all_matches)
    reason = resolution.selected_reason
    if reason == "target-branch":
        detail = f"using #{selected.number} on {selected.branch!r} (your push target)"
    elif reason == "prefer-open":
        detail = f"using open change #{selected.number} on {selected.branch!r}"
    elif reason == "branch-mismatch":
        detail = f"using #{selected.number} on {selected.branch!r} (not your push target)"
    else:
        detail = f"using #{selected.number} on {selected.branch!r}"

    return (
        f"note: Change-Id {selected.change_id} matches {total} change(s) (branches: {branches}); "
        f"{detail}. Override with a triplet or change: number."
    )
