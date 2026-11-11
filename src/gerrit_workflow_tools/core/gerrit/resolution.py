"""What a **changeish** resolved to: the selected change, its alternatives, and the note.

The data model of a resolution, kept apart from the narrowing in
:mod:`~gerrit_workflow_tools.core.gerrit.change_resolution` that produces it. Its JSON form is
the ``resolution`` block of the spec (change-and-commit-identifiers.md §5).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from gerrit_workflow_tools.core.changeish import ChangeishKind
from gerrit_workflow_tools.core.gerrit.stack_context import ChangeResolutionError, build_triplet


SelectedReason = Literal["unique", "target-branch", "prefer-open", "explicit", "branch-mismatch"]


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
