"""What a **changeish** resolved to: the selected change, its alternatives, and the note.

The data model of a resolution, kept apart from the narrowing in
:mod:`~gerrit_workflow_tools.core.gerrit.change_resolution` that produces it. Its JSON form is
the ``resolution`` block of the spec (change-and-commit-identifiers.md §5).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from gerrit_workflow_tools.core.changeish import ChangeishKind, change_label
from gerrit_workflow_tools.core.gerrit.stack_context import ChangeResolutionError, build_triplet
from gerrit_workflow_tools.core.stack import abbreviated_sha
from gerrit_workflow_tools.core.words import counted


SelectedReason = Literal["unique", "target-branch", "prefer-open", "explicit", "branch-mismatch"]


class ChangeAmbiguousError(ChangeResolutionError):
    """Multiple Gerrit changes matched after narrowing."""

    def __init__(self, message: str, *, alternatives: list[SelectedChange]) -> None:
        listed = ", ".join(f"{change_label(alt.number)} ({alt.triplet}, {alt.status})" for alt in alternatives)
        super().__init__(f"{message}: {listed}. Name one by its number or triplet" if listed else message)
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

    @property
    def project(self) -> str:
        """The project segment of :attr:`triplet`."""
        return self.triplet.rsplit("~", 2)[0]

    def to_json_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LocalSibling:
    """A **local stack** commit carrying the selected change's Change-Id, pushing to another branch.

    That commit belongs to its **sibling** on the **push branch**, not to the selected change,
    so it is reported beside the selected change rather than as its ``local_sha``.
    """

    sha: str
    branch: str
    """The **push branch** the commit targets."""
    number: int | None = None
    """The sibling change on :attr:`branch`, when the resolution already saw it."""

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
    local_sibling: LocalSibling | None = None
    shadowed_change: bool = False
    """The input is written like a change number (``2050``, ``c2050``) and named a commit, which
    wins (spec §2.2); the note says how to name the change instead."""

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
        if self.local_sibling is not None:
            out["local_sibling"] = self.local_sibling.to_json_dict()
        return out


def single_query_match(rows: list[dict[str, Any]], raw: str) -> dict[str, Any]:
    """The one change a ``q:`` query matched. None is not found; several are ambiguous, listed."""
    if not rows:
        raise ChangeResolutionError(f"cannot resolve {raw!r}: no Gerrit change matches")
    if len(rows) > 1:
        more = "+" if rows[-1].get("_more_changes") else ""
        alternatives = [SelectedChange.from_change_row(row) for row in rows]
        raise ChangeAmbiguousError(f"ambiguous change query ({len(rows)}{more} matches)", alternatives=alternatives)
    return rows[0]


def shadowed_change_note(raw: str, sha: str) -> str:
    """One line: *raw* named commit *sha*, though it is also written like a change number."""
    return f"note: {raw!r} is commit {abbreviated_sha(sha)} here; for the Gerrit change write change:{raw.lstrip('cC')}"


def local_sibling_note(sibling: LocalSibling, *, branch: str, number: int | None = None) -> str:
    """One line: the change named on *branch* is not the one the **local stack** commit pushes to."""
    named = f"change {change_label(number)}" if number is not None else "that change"
    as_change = f" ({change_label(sibling.number)})" if sibling.number is not None else ""
    return (
        f"note: {named} is on {branch!r}; local stack commit {abbreviated_sha(sibling.sha)} carries the same "
        f"Change-Id but targets {sibling.branch!r}{as_change}."
    )


def format_local_sibling_note(resolution: Resolution) -> str | None:
    """:func:`local_sibling_note` for *resolution*, when it has a :attr:`~Resolution.local_sibling`."""
    sibling, selected = resolution.local_sibling, resolution.selected
    if sibling is None or selected is None:
        return None
    return local_sibling_note(sibling, branch=selected.branch, number=selected.number)


def _elsewhere(changes: list[SelectedChange]) -> str:
    """``dev as c2051, main as c2049 (abandoned)``."""
    return ", ".join(
        f"{alt.branch} as {change_label(alt.number)}" + (f" ({alt.status.lower()})" if alt.status != "NEW" else "")
        for alt in changes
    )


def format_resolution_note(resolution: Resolution) -> str | None:
    """Return a one-line stderr transparency note when narrowing occurred.

    A Change-Id read from a commit's own footer (a git rev, a stack commit in ``ger log``) is
    narrowed to the push target as expected, so its note only says where else it is. A Change-Id
    typed bare gets the full note: which change was picked, why, and how to name another.
    """
    if resolution.shadowed_change and resolution.local_sha is not None:
        return shadowed_change_note(resolution.input, resolution.local_sha)
    footer = resolution.kind == "git-rev"
    selected, alternatives = resolution.selected, resolution.alternatives
    if not resolution.ambiguous or selected is None:
        if selected is None and alternatives:
            if footer:
                return f"note: not on your push target yet; same Change-Id on {_elsewhere(alternatives)}"
            return (
                f"note: Change-Id matches {counted(len(alternatives), 'change')} on "
                f"{'another branch' if len({alt.branch for alt in alternatives}) == 1 else 'other branches'} "
                f"({_elsewhere(alternatives)}); none on your push target."
            )
        return None

    reason = resolution.selected_reason
    if footer:
        if reason == "branch-mismatch":
            return f"note: not on your push target yet; showing {selected.branch} as {change_label(selected.number)}"
        return f"note: also on {_elsewhere(alternatives)} (same Change-Id)"
    all_matches = [selected, *alternatives]
    branches = ", ".join(f"{alt.branch} {change_label(alt.number)}" for alt in all_matches)
    if reason == "target-branch":
        detail = f"using {change_label(selected.number)} on {selected.branch!r} (your push target)"
    elif reason == "prefer-open":
        detail = f"using open change {change_label(selected.number)} on {selected.branch!r}"
    elif reason == "branch-mismatch":
        detail = f"using {change_label(selected.number)} on {selected.branch!r} (not your push target)"
    else:
        detail = f"using {change_label(selected.number)} on {selected.branch!r}"
    other = next((alt for alt in alternatives if alt.number != selected.number), None)
    override = f"Name another by its number{f' ({change_label(other.number)})' if other else ''} or triplet."
    return (
        f"note: Change-Id {selected.change_id} matches {counted(len(all_matches), 'change')} (branches: {branches}); "
        f"{detail}. {override}"
    )
