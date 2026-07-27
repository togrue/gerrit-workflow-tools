"""Shared Gerrit changeish classification, stack context, and resolution."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from gerrit_workflow_tools.core.change_id import extract_valid_change_id
from gerrit_workflow_tools.core.config import gerrit_remote
from gerrit_workflow_tools.core.gerrit.rest import GerritApiError, GerritRest, pick_change_from_query_result
from gerrit_workflow_tools.core.gerrit_project_id import resolve_gerrit_project_name
from gerrit_workflow_tools.core.git_run import GitError, git_out
from gerrit_workflow_tools.core.git_state import (
    effective_gerrit_destination_branch,
    refs_for_push_branch_name,
    resolve_working_branch,
)

ChangeishKind = Literal[
    "git-rev",
    "change-id",
    "triplet",
    "change-number",
    "change-ref",
    "url",
    "query",
]

SelectedReason = Literal["unique", "target-branch", "prefer-open", "explicit", "branch-mismatch"]

_CHANGE_ID_RE = re.compile(r"^[iI][0-9a-fA-F]{40}$")
_CHANGE_REF_RE = re.compile(r"^refs/changes/\d+/\d+/\d+$")
_GERRIT_URL_CHANGE_RE = re.compile(r"/c/(?:[^/]+/)?\+/(\d+)")
_INACTIVE_STATUSES = frozenset({"ABANDONED", "MERGED"})


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


def classify_changeish(s: str) -> ChangeishKind:
    """Classify *s* into a changeish kind per behavior spec §2.1."""
    raw = s.strip()
    if not raw:
        raise ChangeResolutionError("empty changeish")

    lower = raw.lower()
    if lower.startswith(("rev:", "git:")):
        return "git-rev"
    if lower.startswith("change:") or lower.startswith("cl:"):
        return "change-number"
    if lower.startswith("q:"):
        return "query"
    if raw.startswith(("http://", "https://")):
        return "url"
    if _CHANGE_REF_RE.fullmatch(raw):
        return "change-ref"
    if "~" in raw:
        parts = raw.split("~")
        if len(parts) == 3 and _CHANGE_ID_RE.fullmatch(parts[2]):
            return "triplet"
    if _CHANGE_ID_RE.fullmatch(raw):
        return "change-id"
    return "git-rev"


def parse_triplet(s: str) -> tuple[str, str, str]:
    """Split ``project~branch~changeId`` into its three parts."""
    parts = s.strip().split("~")
    if len(parts) != 3 or not all(p.strip() for p in parts):
        raise ChangeResolutionError(f"invalid triplet: {s!r}")
    return parts[0], parts[1], parts[2]


def build_triplet(project: str, branch: str, change_id: str) -> str:
    """Build a Gerrit REST triplet from stack context and a footer Change-Id."""
    return f"{project}~{branch}~{change_id}"


def _stack_branch_name(cwd: Path | str | None, branch: str | None) -> str:
    if branch is not None:
        return branch
    working = resolve_working_branch(cwd)
    if working:
        return working
    raise ChangeResolutionError("cannot determine working branch (detached HEAD with no branch context)")


def resolve_stack_context(cwd: Path | str | None, branch: str | None = None) -> StackContext:
    """Resolve project, target branch, and push branch for the current stack."""
    branch_name = _stack_branch_name(cwd, branch)
    project = resolve_gerrit_project_name(cwd)
    if not project:
        remote = gerrit_remote(cwd)
        raise ChangeResolutionError(
            f"cannot resolve Gerrit project name; set `gerrit.project` or configure remote `{remote}` "
            "with a parseable URL"
        )

    target = effective_gerrit_destination_branch(cwd, branch_name)
    if not target:
        remote = gerrit_remote(cwd)
        raise ChangeResolutionError(
            f"No Gerrit destination branch for branch {branch_name!r}. "
            f"Set upstream to a branch on `{remote}` (`gerrit.remote`), "
            f"e.g. `git branch --set-upstream-to={remote}/<branch>`, "
            f"or configure `branch.{branch_name}.gerritTarget`."
        )

    push_branch = refs_for_push_branch_name(cwd, target)
    return StackContext(project=project, target_branch=target, push_branch=push_branch)


def _strip_force_git_prefix(s: str) -> str:
    lower = s.lower()
    for prefix in ("rev:", "git:"):
        if lower.startswith(prefix):
            return s[len(prefix) :].strip()
    return s.strip()


def _strip_change_number_prefix(s: str) -> str:
    lower = s.lower()
    for prefix in ("change:", "cl:"):
        if lower.startswith(prefix):
            return s[len(prefix) :].strip()
    return s.strip()


def _strip_query_prefix(s: str) -> str:
    if s.lower().startswith("q:"):
        return s[2:].strip()
    return s.strip()


def _parse_gerrit_url_change_number(url: str) -> str:
    m = _GERRIT_URL_CHANGE_RE.search(url)
    if m:
        return m.group(1)
    path = urlparse(url).path or ""
    tail = path.rstrip("/").split("/")[-1]
    if tail.isdigit():
        return tail
    raise ChangeResolutionError(f"cannot parse Gerrit change number from URL: {url!r}")


def _change_number_from_change_ref(ref: str) -> str:
    parts = ref.split("/")
    if len(parts) >= 5 and parts[1] == "changes" and parts[3].isdigit():
        return parts[3]
    raise ChangeResolutionError(f"invalid change ref: {ref!r}")


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


def resolve_changeish(
    ref: str,
    *,
    client: GerritRest,
    cwd: Path | str | None,
    branch: str | None = None,
    explicit_target: bool = False,
) -> Resolution:
    """Resolve *ref* to a :class:`Resolution` using stack context and Gerrit."""
    raw = ref.strip()
    kind = classify_changeish(raw)
    resolution = Resolution(input=raw, kind=kind)
    stack = resolve_stack_context(cwd, branch)

    if kind == "git-rev":
        rev = _strip_force_git_prefix(raw)
        resolution.local_sha = _resolve_local_sha(cwd, rev)
        msg = git_out("log", "-1", "--format=%B", resolution.local_sha, cwd=cwd)
        footer_cid = extract_valid_change_id(msg)
        if footer_cid:
            selected, reason, ambiguous, alternatives = _resolve_change_id(
                footer_cid,
                client=client,
                stack=stack,
                explicit_target=explicit_target,
            )
            resolution.selected = selected
            resolution.selected_reason = reason
            resolution.ambiguous = ambiguous
            resolution.alternatives = alternatives
        return resolution

    if kind == "change-id":
        selected, reason, ambiguous, alternatives = _resolve_change_id(
            raw,
            client=client,
            stack=stack,
            explicit_target=explicit_target,
        )
        resolution.selected = selected
        resolution.selected_reason = reason
        resolution.ambiguous = ambiguous
        resolution.alternatives = alternatives
        return resolution

    if kind == "triplet":
        row = _fetch_change_row(client, raw)
        resolution.selected = SelectedChange.from_change_row(row)
        resolution.selected_reason = "unique"
        return resolution

    if kind == "change-number":
        number = _strip_change_number_prefix(raw)
        if not number.isdigit():
            raise ChangeResolutionError(f"invalid change number: {raw!r}")
        row = _fetch_change_row(client, number)
        resolution.selected = SelectedChange.from_change_row(row)
        resolution.selected_reason = "unique"
        return resolution

    if kind == "change-ref":
        number = _change_number_from_change_ref(raw)
        row = _fetch_change_row(client, number)
        resolution.selected = SelectedChange.from_change_row(row)
        resolution.selected_reason = "unique"
        return resolution

    if kind == "url":
        number = _parse_gerrit_url_change_number(raw)
        row = _fetch_change_row(client, number)
        resolution.selected = SelectedChange.from_change_row(row)
        resolution.selected_reason = "unique"
        return resolution

    if kind == "query":
        query = _strip_query_prefix(raw)
        if not query:
            raise ChangeResolutionError("empty Gerrit query")
        row = _fetch_single_from_query(client, query)
        resolution.selected = SelectedChange.from_change_row(row)
        resolution.selected_reason = "unique"
        return resolution

    raise ChangeResolutionError(f"unsupported changeish kind: {kind!r}")


def resolve_to_stack_sha(
    ref: str,
    *,
    cwd: Path | str | None,
    branch: str | None = None,
    client: GerritRest | None = None,
) -> str:
    """Resolve a changeish to a full SHA on the current local stack."""
    from gerrit_workflow_tools.core.stack import get_stack_snapshot

    raw = ref.strip()
    kind = classify_changeish(raw)

    if kind == "git-rev":
        return git_out("rev-parse", raw, cwd=cwd)

    change_id: str | None = None
    if kind == "change-id":
        change_id = raw
    elif kind == "triplet":
        _, _, change_id = parse_triplet(raw)
    elif kind in ("change-number", "change-ref", "url", "query"):
        if client is None:
            raise ChangeResolutionError(
                f"cannot resolve {kind!r} to a stack commit without a Gerrit client"
            )
        resolution = resolve_changeish(raw, client=client, cwd=cwd, branch=branch, explicit_target=True)
        if resolution.selected is None:
            raise ChangeResolutionError(f"no Gerrit change resolved for {raw!r}")
        change_id = resolution.selected.change_id
    else:
        raise ChangeResolutionError(f"unsupported changeish kind for stack resolution: {kind!r}")

    snap = get_stack_snapshot(cwd, branch)
    matches = [c for c in snap.commits if c.change_id == change_id]
    if not matches:
        raise ChangeResolutionError(f"no commit in current stack with Change-Id {change_id}")
    if len(matches) > 1:
        shorts = [c.short_sha for c in matches]
        raise ChangeAmbiguousError(
            f"ambiguous Change-Id {change_id} in stack ({', '.join(shorts)})",
            alternatives=[],
        )
    return matches[0].sha


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
