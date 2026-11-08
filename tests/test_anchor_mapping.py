# Spec: docu/spec/commands/comment.md § Re-anchoring (ADR-0011)

from __future__ import annotations

from pathlib import Path

import pytest

from gerrit_workflow_tools.core.anchor_mapping import Hunk, MappedLines, map_lines, place_anchor
from gerrit_workflow_tools.core.comment_anchor import Anchor, anchor_at, fit_to_file, parse_location
from gerrit_workflow_tools.core.git_run import git, git_out


LINES = [f"line {n}" for n in range(1, 11)]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    git("init", "-q", cwd=path)
    git("config", "user.name", "t", cwd=path)
    git("config", "user.email", "t@example", cwd=path)
    return path


def _commit(repo: Path, files: dict[str, list[str] | None]) -> str:
    for name, lines in files.items():
        if lines is None:
            git("rm", "-q", name, cwd=repo)
            continue
        (repo / name).write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
        git("add", name, cwd=repo)
    git("commit", "-q", "-m", "c", "--allow-empty", cwd=repo)
    return git_out("rev-parse", "HEAD", cwd=repo)


def _anchor(repo: Path, commit: str, location: str) -> Anchor:
    lines = git_out("show", f"{commit}:{location.split(':')[0]}", cwd=repo).splitlines()
    return anchor_at(repo, commit, fit_to_file(parse_location(location), lines, commit=commit), lines)


def _with(lines: list[str], at: int, new: list[str], drop: int = 0) -> list[str]:
    """*lines* with *drop* lines from 1-based *at* replaced by *new*."""
    return [*lines[: at - 1], *new, *lines[at - 1 + drop :]]


def test_an_insertion_above_moves_the_line_and_keeps_its_text(repo: Path) -> None:
    first = _commit(repo, {"a.txt": LINES})
    anchor = _anchor(repo, first, "a.txt:5")
    shown = _commit(repo, {"a.txt": _with(LINES, 2, ["new 1", "new 2"])})
    placed = place_anchor(repo, anchor, shown)
    assert (placed.line, placed.moved, placed.outdated, placed.anchored_text) == (7, True, False, "line 5")


def test_an_edit_of_the_anchored_line_marks_it_outdated(repo: Path) -> None:
    first = _commit(repo, {"a.txt": LINES})
    anchor = _anchor(repo, first, "a.txt:5")
    shown = _commit(repo, {"a.txt": _with(LINES, 5, ["line five"], drop=1)})
    placed = place_anchor(repo, anchor, shown)
    assert (placed.line, placed.outdated, placed.anchored_line, placed.anchored_text) == (4, True, 5, "line 5")


def test_deleting_the_anchored_line_marks_it_outdated(repo: Path) -> None:
    first = _commit(repo, {"a.txt": LINES})
    anchor = _anchor(repo, first, "a.txt:5-6")
    shown = _commit(repo, {"a.txt": _with(LINES, 1, ["top"], drop=0)[:5] + LINES[6:]})
    placed = place_anchor(repo, anchor, shown)
    # "top" pushed line 4 to line 5: the outdated comment sits right after it.
    assert (placed.line, placed.start_line, placed.outdated) == (5, None, True)
    assert placed.anchored_text == "line 5\nline 6"


def test_a_range_over_an_untouched_block_survives_an_edit_elsewhere(repo: Path) -> None:
    first = _commit(repo, {"a.txt": LINES})
    anchor = _anchor(repo, first, "a.txt:4-6")
    edited = _with(_with(LINES, 9, ["line nine"], drop=1), 1, ["head"], drop=1)
    placed = place_anchor(repo, anchor, _commit(repo, {"a.txt": _with(edited, 2, ["extra"])}))
    assert (placed.start_line, placed.line, placed.outdated) == (5, 7, False)


def test_a_rename_is_followed(repo: Path) -> None:
    first = _commit(repo, {"a.txt": LINES})
    anchor = _anchor(repo, first, "a.txt:5")
    git("mv", "a.txt", "b.txt", cwd=repo)
    shown = _commit(repo, {"b.txt": _with(LINES, 1, ["new"])})
    placed = place_anchor(repo, anchor, shown)
    assert (placed.path, placed.line, placed.outdated) == ("b.txt", 6, False)


def test_a_rename_is_found_by_blob_once_the_anchor_commit_is_gone(repo: Path) -> None:
    first = _commit(repo, {"a.txt": LINES})
    anchor = _anchor(repo, first, "a.txt:5")
    (repo / "moved").mkdir()
    git("mv", "a.txt", "moved/a.txt", cwd=repo)
    shown = _commit(repo, {})
    gone = Anchor(commit="0" * 40, location=anchor.location, blob=anchor.blob, text=anchor.text)
    placed = place_anchor(repo, gone, shown)
    assert (placed.path, placed.line, placed.moved, placed.outdated) == ("moved/a.txt", 5, True, False)


def test_a_deleted_file_leaves_an_outdated_file_comment(repo: Path) -> None:
    first = _commit(repo, {"a.txt": LINES, "keep.txt": ["x"]})
    anchor = _anchor(repo, first, "a.txt:5")
    placed = place_anchor(repo, anchor, _commit(repo, {"a.txt": None}))
    assert (placed.path, placed.line, placed.outdated, placed.anchored_text) == ("a.txt", None, True, "line 5")


def test_the_mapping_is_direct_from_the_anchor(repo: Path) -> None:
    first = _commit(repo, {"a.txt": LINES})
    anchor = _anchor(repo, first, "a.txt:5")
    # Rewrite 1 edits line 5 away; rewrite 2 restores it. Chained, the comment would stay
    # outdated; mapped straight from the anchor, it is a clean move.
    _commit(repo, {"a.txt": _with(LINES, 5, ["changed"], drop=1)})
    placed = place_anchor(repo, anchor, _commit(repo, {"a.txt": _with(LINES, 1, ["new"])}))
    assert (placed.line, placed.outdated) == (6, False)


def test_the_same_blob_is_not_moved(repo: Path) -> None:
    first = _commit(repo, {"a.txt": LINES})
    anchor = _anchor(repo, first, "a.txt:5")
    placed = place_anchor(repo, anchor, _commit(repo, {"other.txt": ["x"]}))
    assert (placed.line, placed.moved, placed.outdated) == (5, False, False)


@pytest.mark.parametrize(
    ("hunks", "start", "end", "expected"),
    [
        ((Hunk(5, 0, 6, 2),), 5, 5, MappedLines(5, 5, False)),  # insertion right after the line
        ((Hunk(4, 0, 5, 2),), 5, 5, MappedLines(7, 7, False)),  # insertion right before it
        ((Hunk(5, 0, 6, 1),), 4, 6, MappedLines(4, 7, False)),  # insertion inside a range widens it
        ((Hunk(1, 1, 0, 0),), 1, 1, MappedLines(1, 1, True)),  # deleted first line
        ((Hunk(2, 2, 1, 0), Hunk(8, 1, 7, 1)), 5, 5, MappedLines(3, 3, False)),  # deletion above
    ],
)
def test_map_lines(hunks: tuple[Hunk, ...], start: int, end: int, expected: MappedLines) -> None:
    assert map_lines(hunks, start, end) == expected
