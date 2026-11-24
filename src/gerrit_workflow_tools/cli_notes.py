"""Transparency notes: how a changeish resolved, and what else a command wants known, dimmed on stderr."""

from __future__ import annotations

from gerrit_workflow_tools.core.gerrit.change_resolution import (
    Resolution,
    StackCommit,
    format_local_sibling_note,
    format_resolution_note,
)
from gerrit_workflow_tools.render.wrap import say


def print_note(note: str | None) -> None:
    """Print a transparency note, dimmed, on stderr so stdout stays machine-readable."""
    if note:
        say(note, dim=True)


def print_resolution_note(resolution: Resolution | None) -> None:
    """Print how *resolution* narrowed, when Gerrit was consulted and it did, and any local sibling."""
    if resolution is not None:
        print_note(format_resolution_note(resolution))
        print_note(format_local_sibling_note(resolution))


def print_stack_commit_notes(target: StackCommit) -> None:
    """:func:`print_resolution_note` for a changeish resolved to a stack commit."""
    if target.resolution is not None:
        print_note(format_resolution_note(target.resolution))
    print_note(target.note)
