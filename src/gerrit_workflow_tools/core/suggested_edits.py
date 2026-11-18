"""Suggested edits on an inline comment: Gerrit fix suggestions and ```` ```suggestion ```` blocks.

A reviewer suggests an edit in one of two ways. Gerrit's ``CommentInfo.fix_suggestions``
(``FixSuggestionInfo``: ``fix_id``, ``description``, ``replacements``) says it exactly:
a path, a range and the text that replaces it. The web UI's "Suggest edit" may instead
write a fenced ```` ```suggestion ```` block into the comment's text: the lines the comment
is on, as the reviewer would have them. Both are normalised here into one shape,
:class:`SuggestedEdit`; the comment's message itself is left as it was written.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from gerrit_workflow_tools.core.comment_anchor import CommentRange


# A fence opened by ```suggestion on a line of its own, closed by the first line that is only
# ``` (as Gerrit's web UI reads it), with LF or CRLF line endings.
_SUGGESTION_BLOCK = re.compile(r"^[ \t]*```suggestion[ \t]*\r?\n(.*?)^[ \t]*```[ \t]*\r?$", re.MULTILINE | re.DOTALL)


@dataclass(frozen=True)
class FixReplacement:
    """Gerrit's ``FixReplacementInfo``: *replacement* takes the place of *range* in *path*."""

    path: str
    range: CommentRange
    replacement: str

    @property
    def inserts(self) -> bool:
        """The range is empty: the text goes in before it, replacing nothing."""
        rng = self.range
        return (rng.start_line, rng.start_character) == (rng.end_line, rng.end_character)

    def describe(self) -> str:
        """The range it replaces, as a plain range with 1-based lines and columns.

        ``path:5`` and ``path:5-7`` for whole lines. Otherwise ``path:L.C-L.C``: the first
        character replaced, and the end as the number of characters of the end line it covers
        (so ``5.3-5.9`` is characters 3 to 9 of line 5, ``5.3-7.0`` runs up to the start of
        line 7). An insertion is the one position it goes in before: ``path:5.3``.
        """
        rng = self.range
        start, end = rng.start_line, rng.end_line
        if self.inserts:
            return f"{self.path}:{start}.{rng.start_character + 1}"
        if rng.start_character == 0 and rng.end_character == 0 and end > start:
            # Whole lines; the end line is not touched.
            return f"{self.path}:{start}" if end - 1 == start else f"{self.path}:{start}-{end - 1}"
        return f"{self.path}:{start}.{rng.start_character + 1}-{end}.{rng.end_character}"

    def lines(self) -> list[str]:
        """The replacement's lines, without line endings; a final line ending adds no line."""
        text = self.replacement.replace("\r\n", "\n")
        return (text[:-1] if text.endswith("\n") else text).split("\n")

    def to_json(self) -> dict[str, Any]:
        """Gerrit's own shape (``path``, ``range``, ``replacement``), and ``location`` as :meth:`describe` writes it."""
        return {
            "path": self.path,
            "range": self.range.as_gerrit(),
            "replacement": self.replacement,
            "location": self.describe(),
        }


@dataclass(frozen=True)
class SuggestedEdit:
    """A reviewer's suggested change to the code a comment is on."""

    description: str
    replacements: tuple[FixReplacement, ...]
    fix_id: str | None = None
    """Gerrit's ``fix_id``, the key its REST "apply fix" takes; ``None`` for a block in the text."""
    in_message: bool = False
    """A ```` ```suggestion ```` block of the comment's text: the message already shows it."""

    def to_json(self) -> dict[str, Any]:
        """``fix_id``, ``description``, ``in_message`` and Gerrit-shaped ``replacements``."""
        return {
            "fix_id": self.fix_id,
            "description": self.description,
            "in_message": self.in_message,
            "replacements": [r.to_json() for r in self.replacements],
        }


def _range_from_raw(raw: Any) -> CommentRange | None:
    if not isinstance(raw, dict):
        return None
    values = [raw.get(key) for key in ("start_line", "start_character", "end_line", "end_character")]
    ints = [v for v in values if isinstance(v, int)]
    return CommentRange(*ints) if len(ints) == len(values) else None


def fix_suggestions_from_raw(raw: Any) -> tuple[SuggestedEdit, ...]:
    """``CommentInfo.fix_suggestions`` as suggested edits; a malformed replacement is left out."""
    if not isinstance(raw, list):
        return ()
    edits: list[SuggestedEdit] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        replacements: list[FixReplacement] = []
        for rep in item.get("replacements") or []:
            rng = _range_from_raw(rep.get("range")) if isinstance(rep, dict) else None
            path, text = (rep.get("path"), rep.get("replacement")) if isinstance(rep, dict) else (None, None)
            if rng is not None and isinstance(path, str) and isinstance(text, str):
                replacements.append(FixReplacement(path=path, range=rng, replacement=text))
        if not replacements:
            continue
        fix_id, description = item.get("fix_id"), item.get("description")
        edits.append(
            SuggestedEdit(
                description=description if isinstance(description, str) else "",
                replacements=tuple(replacements),
                fix_id=fix_id if isinstance(fix_id, str) and fix_id else None,
            )
        )
    return tuple(edits)


def message_suggestions(path: str, message: str, start_line: int | None, line: int | None) -> tuple[SuggestedEdit, ...]:
    """The ```` ```suggestion ```` blocks of *message*: each replaces the lines the comment is on.

    A block opens with a line ```` ```suggestion ```` and ends at the first line that is only
    ```` ``` ````, as Gerrit's web UI reads it; CRLF line endings become LF. A comment with no
    line (on the whole file) has no lines to replace, so it has none.
    """
    if line is None:
        return ()
    first = start_line if start_line is not None and start_line <= line else line
    whole_lines = CommentRange(start_line=first, start_character=0, end_line=line + 1, end_character=0)
    edits: list[SuggestedEdit] = []
    for match in _SUGGESTION_BLOCK.finditer(message):
        body = match.group(1).replace("\r\n", "\n")
        replacement = FixReplacement(path=path, range=whole_lines, replacement=body)
        edits.append(SuggestedEdit(description="", replacements=(replacement,), in_message=True))
    return tuple(edits)


def suggested_edits_from_raw(
    path: str, comment: dict[str, Any], *, start_line: int | None, line: int | None
) -> tuple[SuggestedEdit, ...]:
    """Every suggested edit of one ``CommentInfo``: its fix suggestions, then its text's blocks.

    A block whose text a fix suggestion on the same file already carries is the same edit
    written twice, and is left out.
    """
    fixes = fix_suggestions_from_raw(comment.get("fix_suggestions"))
    message = comment.get("message")
    blocks = message_suggestions(path, message, start_line, line) if isinstance(message, str) else ()
    carried = {(r.path, r.replacement.rstrip("\n")) for edit in fixes for r in edit.replacements}
    fresh = tuple(b for b in blocks if (path, b.replacements[0].replacement.rstrip("\n")) not in carried)
    return fixes + fresh
