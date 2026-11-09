# Spec: docu/spec/commands/comment.md § Re-anchoring (ADR-0011)

from __future__ import annotations

from pathlib import Path

import pytest

from gerrit_workflow_tools.core.anchor_mapping import Hunk, MappedLines, blob_hunks, map_lines, place_anchor
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
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
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
    assert (placed.shown.line, placed.moved, placed.outdated, placed.text) == (7, True, False, "line 5")


def test_an_edit_of_the_anchored_line_marks_it_outdated(repo: Path) -> None:
    first = _commit(repo, {"a.txt": LINES})
    anchor = _anchor(repo, first, "a.txt:5")
    shown = _commit(repo, {"a.txt": _with(LINES, 5, ["line five"], drop=1)})
    placed = place_anchor(repo, anchor, shown)
    assert (placed.shown.line, placed.outdated, placed.anchored.line, placed.text) == (4, True, 5, "line 5")


def test_deleting_the_anchored_line_marks_it_outdated(repo: Path) -> None:
    first = _commit(repo, {"a.txt": LINES})
    anchor = _anchor(repo, first, "a.txt:5-6")
    shown = _commit(repo, {"a.txt": _with(LINES, 1, ["top"], drop=0)[:5] + LINES[6:]})
    placed = place_anchor(repo, anchor, shown)
    # "top" pushed line 4 to line 5: the outdated comment sits right after it.
    assert (placed.shown.describe(), placed.outdated) == ("a.txt:5", True)
    assert placed.text == "line 5\nline 6"


def test_a_range_over_an_untouched_block_survives_an_edit_elsewhere(repo: Path) -> None:
    first = _commit(repo, {"a.txt": LINES})
    anchor = _anchor(repo, first, "a.txt:4-6")
    edited = _with(_with(LINES, 9, ["line nine"], drop=1), 1, ["head"], drop=1)
    placed = place_anchor(repo, anchor, _commit(repo, {"a.txt": _with(edited, 2, ["extra"])}))
    assert (placed.shown.start_line, placed.shown.line, placed.outdated) == (5, 7, False)


def test_a_rename_is_followed(repo: Path) -> None:
    first = _commit(repo, {"a.txt": LINES})
    anchor = _anchor(repo, first, "a.txt:5")
    git("mv", "a.txt", "b.txt", cwd=repo)
    shown = _commit(repo, {"b.txt": _with(LINES, 1, ["new"])})
    placed = place_anchor(repo, anchor, shown)
    assert (placed.shown.path, placed.shown.line, placed.outdated) == ("b.txt", 6, False)


def test_a_rename_is_found_by_blob_once_the_anchor_commit_is_gone(repo: Path) -> None:
    first = _commit(repo, {"a.txt": LINES})
    anchor = _anchor(repo, first, "a.txt:5")
    (repo / "moved").mkdir()
    git("mv", "a.txt", "moved/a.txt", cwd=repo)
    shown = _commit(repo, {})
    gone = Anchor(commit="0" * 40, location=anchor.location, blob=anchor.blob, text=anchor.text)
    placed = place_anchor(repo, gone, shown)
    assert (placed.shown.path, placed.shown.line, placed.moved, placed.outdated) == ("moved/a.txt", 5, True, False)


def test_a_deleted_file_leaves_an_outdated_file_comment(repo: Path) -> None:
    first = _commit(repo, {"a.txt": LINES, "keep.txt": ["x"]})
    anchor = _anchor(repo, first, "a.txt:5")
    placed = place_anchor(repo, anchor, _commit(repo, {"a.txt": None}))
    assert (placed.shown.path, placed.shown.line, placed.outdated, placed.text) == ("a.txt", None, True, "line 5")


def test_two_successive_rewrites_map_the_same_as_one(repo: Path) -> None:
    first = _commit(repo, {"a.txt": LINES})
    anchor = _anchor(repo, first, "a.txt:5")
    # Rewrite 1 edits line 5 away: mapped onto it, the comment is outdated.
    edited = _commit(repo, {"a.txt": _with(LINES, 5, ["changed"], drop=1)})
    assert place_anchor(repo, anchor, edited).outdated
    # Rewrite 2 restores line 5 below a new first line. Chained through rewrite 1, the
    # comment would stay outdated; mapped from the anchor, it is the same clean move as
    # the one rewrite straight from the anchor's commit.
    twice = place_anchor(repo, anchor, _commit(repo, {"a.txt": _with(LINES, 1, ["new"])}))
    git("checkout", "-q", "-b", "once", first, cwd=repo)
    once = place_anchor(repo, anchor, _commit(repo, {"a.txt": _with(LINES, 1, ["new"])}))
    assert twice == once
    assert (twice.shown.line, twice.outdated) == (6, False)


def test_user_diff_config_does_not_merge_hunks(repo: Path) -> None:
    git("config", "diff.interHunkContext", "5", cwd=repo)
    first = _commit(repo, {"a.txt": LINES})
    anchor = _anchor(repo, first, "a.txt:3")
    edited = _with(_with(LINES, 5, ["line five"], drop=1), 1, ["line one"], drop=1)
    placed = place_anchor(repo, anchor, _commit(repo, {"a.txt": edited}))
    assert (placed.shown.line, placed.outdated) == (3, False)


def test_renames_are_found_from_a_subdirectory(repo: Path) -> None:
    git("config", "diff.relative", "true", cwd=repo)
    first = _commit(repo, {"src/a.txt": LINES, "src/keep.txt": ["x"]})
    anchor = _anchor(repo, first, "src/a.txt:5")
    (repo / "lib").mkdir()
    git("mv", "src/a.txt", "lib/a.txt", cwd=repo)
    shown = _commit(repo, {"lib/a.txt": _with(LINES, 1, ["new"])})
    placed = place_anchor(repo / "src", anchor, shown)
    assert (placed.shown.path, placed.shown.line) == ("lib/a.txt", 6)
    gone = Anchor(commit="0" * 40, location=anchor.location, blob=anchor.blob, text=anchor.text)
    git("mv", "lib/a.txt", "lib/b.txt", cwd=repo)
    (repo / "lib" / "b.txt").write_text("".join(f"{line}\n" for line in LINES), encoding="utf-8")
    git("add", "lib/b.txt", cwd=repo)
    by_blob = place_anchor(repo / "src", gone, _commit(repo, {}))
    assert (by_blob.shown.path, by_blob.shown.line) == ("lib/b.txt", 5)


def test_deleting_the_last_line_marks_it_outdated(repo: Path) -> None:
    first = _commit(repo, {"a.txt": LINES})
    anchor = _anchor(repo, first, "a.txt:10")
    placed = place_anchor(repo, anchor, _commit(repo, {"a.txt": LINES[:9]}))
    assert (placed.shown.line, placed.outdated) == (9, True)


def test_an_emptied_file_leaves_an_outdated_file_comment(repo: Path) -> None:
    first = _commit(repo, {"a.txt": LINES})
    anchor = _anchor(repo, first, "a.txt:5")
    placed = place_anchor(repo, anchor, _commit(repo, {"a.txt": []}))
    assert (placed.shown.path, placed.shown.line, placed.outdated) == ("a.txt", None, True)


def test_a_binary_file_leaves_an_outdated_file_comment(repo: Path) -> None:
    first = _commit(repo, {"a.txt": LINES})
    anchor = _anchor(repo, first, "a.txt:5")
    (repo / "a.txt").write_bytes(b"\0binary\0" * 10)
    git("add", "a.txt", cwd=repo)
    placed = place_anchor(repo, anchor, _commit(repo, {}))
    assert (placed.shown.path, placed.shown.line, placed.outdated) == ("a.txt", None, True)


def test_a_failed_diff_is_not_cached(repo: Path) -> None:
    first = _commit(repo, {"a.txt": LINES})
    blob = git_out("rev-parse", f"{first}:a.txt", cwd=repo)
    # Content no other test writes: the cache is keyed by content and shared by every repo.
    later = "".join(f"{line}\n" for line in ["only in this test", *LINES])
    missing = git("hash-object", "--stdin", cwd=repo, input=later).stdout.strip()
    assert blob_hunks(repo, blob, missing) is None
    git("hash-object", "-w", "--stdin", cwd=repo, input=later)
    assert blob_hunks(repo, blob, missing) == (Hunk(0, 0, 1, 1),)


def test_the_same_blob_is_not_moved(repo: Path) -> None:
    first = _commit(repo, {"a.txt": LINES})
    anchor = _anchor(repo, first, "a.txt:5")
    placed = place_anchor(repo, anchor, _commit(repo, {"other.txt": ["x"]}))
    assert (placed.shown.line, placed.moved, placed.outdated) == (5, False, False)


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
