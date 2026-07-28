"""Shared Gerrit changeish classification, stack context, and resolution."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from gerrit_workflow_tools.core.change_id import extract_valid_change_id
from gerrit_workflow_tools.core.changeish import Changeish, ChangeishKind, parse
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.rest import (
    GerritApiError,
    GerritRest,
    norm_change_id,
    pick_change_from_query_result,
)
from gerrit_workflow_tools.core.gerrit_project_id import resolve_gerrit_project_name
from gerrit_workflow_tools.core.git_run import GitError, git, git_out
from gerrit_workflow_tools.core.git_state import (
    effective_gerrit_destination_branch,
    refs_for_push_branch_name,
    resolve_working_branch,
)

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
    "StackContext",
    "build_triplet",
    "format_resolution_note",
    "resolution_fields_from_change_rows",
    "resolution_from_change_rows",
    "resolve_changeish",
    "resolve_stack_context",
    "resolve_to_stack_sha",
]


class ChangeResolutionError(RuntimeError):
    """Changeish could not be resolved (not found, invalid input, missing stack context)."""


class ChangeAmbiguousError(ChangeResolutionError):
    """Multiple Gerrit changes matched after narrowing."""

    def __init__(self, message: str, *, alternatives: list[SelectedChange]) -> None:
        super().__init__(message)
        self.alternatives = alternatives


@dataclass(frozen=True)
class StackContext:
    """Repo/branch context for triplet building and Change-Id narrowing."""

    project: str
    target_branch: str
    push_branch: str


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
        if not isinstance(number, int):
            raise ChangeResolutionError("Gerrit change row missing _number")
        if not isinstance(triplet, str) or not triplet:
            raise ChangeResolutionError("Gerrit change row missing id (triplet)")
        if not isinstance(branch, str) or not branch:
            raise ChangeResolutionError("Gerrit change row missing branch")
        if not isinstance(change_id, str) or not change_id:
            raise ChangeResolutionError("Gerrit change row missing change_id")
        if not isinstance(status, str):
            status = "UNKNOWN"
        return cls(
            number=number,
            triplet=triplet,
            branch=branch,
            change_id=change_id,
            status=status,
        )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "triplet": self.triplet,
            "branch": self.branch,
            "change_id": self.change_id,
            "status": self.status,
        }


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
    """Parse *s*, rejecting blank input.

    The grammar itself is total (see :func:`gerrit_workflow_tools.core.changeish.parse`);
    refusing an empty changeish is a resolution decision, so it lives here.
    """
    parsed = parse(s)
    if not parsed.raw:
        raise ChangeResolutionError("empty changeish")
    return parsed


def build_triplet(project: str, branch: str, change_id: str) -> str:
    """Build a Gerrit REST triplet from stack context and a footer Change-Id."""
    return f"{project}~{branch}~{change_id}"


def _stack_branch_name(cwd: Path | str | None, branch: str | None, *, settings: Settings) -> str:
    if branch is not None:
        return branch
    working = resolve_working_branch(cwd, settings=settings)
    if working:
        return working
    raise ChangeResolutionError("cannot determine working branch (detached HEAD with no branch context)")


def resolve_stack_context(cwd: Path | str | None, branch: str | None = None, *, settings: Settings) -> StackContext:
    """Resolve project, target branch, and push branch for the current stack."""
    branch_name = _stack_branch_name(cwd, branch, settings=settings)
    project = resolve_gerrit_project_name(cwd, settings=settings)
    if not project:
        remote = settings.gerrit_remote
        raise ChangeResolutionError(
            f"cannot resolve Gerrit project name; set `gerrit.project` or configure remote `{remote}` "
            "with a parseable URL"
        )

    target = effective_gerrit_destination_branch(cwd, branch_name, settings=settings)
    if not target:
        remote = settings.gerrit_remote
        raise ChangeResolutionError(
            f"No Gerrit destination branch for branch {branch_name!r}. "
            f"Set upstream to a branch on `{remote}` (`gerrit.remote`), "
            f"e.g. `git branch --set-upstream-to={remote}/<branch>`, "
            f"or configure `branch.{branch_name}.gerritTarget`."
        )

    push_branch = refs_for_push_branch_name(target, settings=settings)
    return StackContext(project=project, target_branch=target, push_branch=push_branch)


def _local_sha_or_none(cwd: Path | str | None, rev: str) -> str | None:
    """Full SHA for *rev* when the repository can resolve it to a commit, else ``None``."""
    p = git("rev-parse", "-q", "--verify", f"{rev}^{{commit}}", cwd=cwd, check=False)
    if p.returncode != 0:
        return None
    return p.stdout.strip() or None


def _verified_local_sha(cwd: Path | str | None, rev: str) -> str:
    """Full SHA for *rev*, with a message that names the input rather than git's internals."""
    sha = _local_sha_or_none(cwd, rev)
    if sha is None:
        raise ChangeResolutionError(f"not a valid commit-ish: {rev!r}")
    return sha


def _resolve_local_sha(cwd: Path | str | None, rev: str) -> str:
    try:
        return git_out("rev-parse", rev, cwd=cwd)
    except GitError as e:
        raise ChangeResolutionError(f"cannot resolve git revision {rev!r}: {e}") from e


def _fetch_change_row(client: GerritRest, change_key: str) -> dict[str, Any]:
    try:
        return client.get_change(change_key)
    except GerritApiError as e:
        raise ChangeResolutionError(f"Gerrit change not found: {change_key!r}") from e


def _query_changes(client: GerritRest, query: str, *, n: int = 25) -> list[dict[str, Any]]:
    try:
        return client.query_changes(query, n=n)
    except GerritApiError as e:
        raise ChangeResolutionError(f"Gerrit query failed: {e}") from e


def _fetch_single_from_query(client: GerritRest, query: str) -> dict[str, Any]:
    rows = _query_changes(client, query)
    try:
        return pick_change_from_query_result(rows)
    except GerritApiError as e:
        raise ChangeResolutionError(str(e)) from e


def _is_active_status(status: str) -> bool:
    return status.upper() not in _INACTIVE_STATUSES


def _narrow_change_id_matches(
    rows: list[dict[str, Any]],
    *,
    push_branch: str,
    explicit_target: bool,
) -> tuple[SelectedChange | None, SelectedReason | None, bool, list[SelectedChange]]:
    """Apply spec §3.1 narrowing for bare Change-Id query results."""
    if not rows:
        return None, None, False, []

    selected_rows = [row for row in rows if isinstance(row.get("branch"), str) and row["branch"] == push_branch]
    other_rows = [row for row in rows if row not in selected_rows]
    ambiguous = len(rows) > 1
    alternatives = [SelectedChange.from_change_row(row) for row in other_rows]

    if len(selected_rows) == 1:
        return (
            SelectedChange.from_change_row(selected_rows[0]),
            "target-branch",
            ambiguous,
            alternatives,
        )

    if len(selected_rows) > 1:
        active = [row for row in selected_rows if _is_active_status(str(row.get("status", "")))]
        inactive = [row for row in selected_rows if not _is_active_status(str(row.get("status", "")))]
        if len(active) == 1:
            inactive_alts = [SelectedChange.from_change_row(row) for row in inactive]
            return (
                SelectedChange.from_change_row(active[0]),
                "prefer-open",
                True,
                alternatives + inactive_alts,
            )
        if len(active) > 1:
            candidates = [SelectedChange.from_change_row(row) for row in active]
            raise ChangeAmbiguousError(
                f"ambiguous Change-Id: {len(active)} open changes on branch {push_branch!r}",
                alternatives=candidates,
            )
        candidates = [SelectedChange.from_change_row(row) for row in selected_rows]
        raise ChangeAmbiguousError(
            f"ambiguous Change-Id: {len(selected_rows)} changes on branch {push_branch!r}",
            alternatives=candidates,
        )

    if not explicit_target:
        return None, None, len(rows) > 1, [SelectedChange.from_change_row(row) for row in rows]

    if len(rows) == 1:
        picked = SelectedChange.from_change_row(rows[0])
        return picked, "branch-mismatch", True, []

    candidates = [SelectedChange.from_change_row(row) for row in rows]
    raise ChangeAmbiguousError(
        f"ambiguous Change-Id: matches changes on other branches (not {push_branch!r})",
        alternatives=candidates,
    )


def _resolve_change_id(
    change_id: str,
    *,
    client: GerritRest,
    stack: StackContext,
    explicit_target: bool,
) -> tuple[SelectedChange | None, SelectedReason | None, bool, list[SelectedChange]]:
    rows = _query_changes(client, f"change:{change_id}")
    return resolution_fields_from_change_rows(
        rows,
        push_branch=stack.push_branch,
        explicit_target=explicit_target,
    )


def resolution_fields_from_change_rows(
    rows: list[dict[str, Any]],
    *,
    push_branch: str,
    explicit_target: bool,
) -> tuple[SelectedChange | None, SelectedReason | None, bool, list[SelectedChange]]:
    """Narrow ChangeInfo rows for one Change-Id without contacting Gerrit."""
    if len(rows) == 1:
        only = SelectedChange.from_change_row(rows[0])
        if only.branch == push_branch:
            return only, "unique", False, []
        if explicit_target:
            return only, "branch-mismatch", True, []
        return None, None, False, [only]

    return _narrow_change_id_matches(
        rows,
        push_branch=push_branch,
        explicit_target=explicit_target,
    )


def resolution_from_change_rows(
    change_id: str,
    rows: list[dict[str, Any]],
    *,
    push_branch: str,
    explicit_target: bool = False,
) -> Resolution:
    """Build a :class:`Resolution` from already-fetched/cached ChangeInfo rows."""
    selected, reason, ambiguous, alternatives = resolution_fields_from_change_rows(
        rows,
        push_branch=push_branch,
        explicit_target=explicit_target,
    )
    return Resolution(
        input=change_id,
        kind="change-id",
        selected=selected,
        selected_reason=reason,
        ambiguous=ambiguous,
        alternatives=alternatives,
    )


def _fetch_unique_row(parsed: Changeish, client: GerritRest) -> dict[str, Any]:
    """Fetch the one ChangeInfo a directly-addressed changeish names.

    Triplet, change number, ``refs/changes/…`` ref, URL and query all name exactly one change
    and differ only in how the address is spelled — which the grammar has already worked out.
    """
    if parsed.kind == "triplet":
        return _fetch_change_row(client, parsed.raw)

    if parsed.kind == "query":
        if not parsed.query:
            raise ChangeResolutionError("empty Gerrit query")
        return _fetch_single_from_query(client, parsed.query)

    number = parsed.number
    if parsed.kind == "change-number" and not (number and number.isdigit()):
        raise ChangeResolutionError(f"invalid change number: {parsed.raw!r}")
    if number is None:
        raise ChangeResolutionError(f"cannot parse Gerrit change number from URL: {parsed.raw!r}")
    return _fetch_change_row(client, number)


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
    stack = resolve_stack_context(cwd, branch, settings=settings)

    # A git rev and a bare Change-Id both go through Change-Id narrowing; they differ only in
    # where the Change-Id comes from. Everything else addresses one change directly.
    change_id = parsed.change_id
    if parsed.kind == "git-rev":
        resolution.local_sha = _resolve_local_sha(cwd, parsed.rev or parsed.raw)
        msg = git_out("log", "-1", "--format=%B", resolution.local_sha, cwd=cwd)
        change_id = extract_valid_change_id(msg)
        if not change_id:
            return resolution

    if change_id is not None and parsed.kind in ("git-rev", "change-id"):
        selected, reason, ambiguous, alternatives = _resolve_change_id(
            change_id,
            client=client,
            stack=stack,
            explicit_target=explicit_target,
        )
        resolution.selected = selected
        resolution.selected_reason = reason
        resolution.ambiguous = ambiguous
        resolution.alternatives = alternatives
        return resolution

    resolution.selected = SelectedChange.from_change_row(_fetch_unique_row(parsed, client))
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


def resolve_to_stack_sha(
    ref: str,
    *,
    cwd: Path | str | None,
    settings: Settings,
    branch: str | None = None,
    client_factory: Callable[[], GerritRest] | None = None,
) -> StackCommit:
    """Resolve a changeish to a full SHA on the current local stack.

    Never fetches. Kinds that carry a Change-Id resolve without touching the network; kinds
    that address a change by number cost one Gerrit round trip to learn the id first.

    *client_factory* is called only on the paths that genuinely need Gerrit, so a caller can
    defer resolving ``gerrit.webUrl`` — a ``refs/changes/…`` ref the repository already has
    must not require Gerrit configuration to use.
    """
    from gerrit_workflow_tools.core.stack import get_stack_snapshot

    parsed = parse_changeish(ref)

    if parsed.kind == "git-rev":
        return StackCommit(sha=_verified_local_sha(cwd, parsed.raw))

    if parsed.kind == "change-ref":
        # A refs/changes ref you already have is just a git ref. Asking Gerrit to translate a
        # ref the repository can resolve on its own would be a round trip for nothing.
        local = _local_sha_or_none(cwd, parsed.raw)
        if local is not None:
            return StackCommit(sha=local)

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

    cid = selected.change_id
    return (
        f"note: Change-Id {cid} matches {total} change(s) (branches: {branches}); "
        f"{detail}. Override with a triplet or change: number."
    )
