"""The review worktree's folder when something is already there (core/review_worktree.py)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.core.git_run import git, git_out
from gerrit_workflow_tools.core.review_worktree import ReviewWorktreeBlocked
from gerrit_workflow_tools.server.api import GerApi
from tests.change_store import ChangeStore
from tests.test_review import _review, _worktree, gerrit  # noqa: F401  (the fixture)
from tests.test_serve import _post


Gerrit = tuple[ChangeStore, list[str], list[str]]


def _left_over(repo: Path, store: ChangeStore, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A review worktree whose entry in the repository is gone, with an edit of yours in it."""
    assert _review(repo, store, ["101"], monkeypatch)[0] == ExitCode.OK
    worktree = _worktree(repo)
    (worktree / "mine.txt").write_text("an edit\n", encoding="utf-8")
    shutil.rmtree(Path(git_out("rev-parse", "--absolute-git-dir", cwd=worktree)))
    return worktree


def test_a_left_over_folder_is_moved_aside_and_the_review_opens(
    stack_repo: Path,
    gerrit: Gerrit,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, _keys, shas = gerrit
    worktree = _left_over(stack_repo, store, monkeypatch)
    code, _out, err = _review(stack_repo, store, ["102", "--no-stage"], monkeypatch)
    assert code == ExitCode.OK
    aside = worktree.with_name(f"{worktree.name}.left-over")
    assert f"it is kept as {aside}" in err
    assert (aside / "mine.txt").read_text(encoding="utf-8") == "an edit\n", "nothing in it was deleted"
    assert git_out("rev-parse", "HEAD", cwd=worktree) == shas[2]
    assert not (worktree / "mine.txt").exists()


def test_a_second_left_over_gets_its_own_name_and_serve_says_where_it_went(
    stack_repo: Path,
    gerrit: Gerrit,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, _keys, _shas = gerrit
    worktree = _left_over(stack_repo, store, monkeypatch)
    worktree.with_name(f"{worktree.name}.left-over").mkdir()
    answer = _post(GerApi(stack_repo, gerrit=store), "/review/open", {"change": 102})
    assert answer["set_aside"] == str(worktree.with_name(f"{worktree.name}.left-over-2"))
    assert answer["review"]["number"] == 102
    again = _post(GerApi(stack_repo, gerrit=store), "/review/open", {"change": 103})
    assert again["set_aside"] is None, "the worktree it made is reused"


@pytest.mark.parametrize("what", ["files", "repository", "worktree"])
def test_a_folder_that_is_not_gers_is_left_alone_and_the_ways_on_are_named(
    what: str,
    stack_repo: Path,
    gerrit: Gerrit,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, _keys, _shas = gerrit
    worktree = _worktree(stack_repo)
    if what == "files":
        worktree.mkdir()
        (worktree / "notes.txt").write_text("mine\n", encoding="utf-8")
        words = "the folder holds other files"
    elif what == "repository":
        git("clone", "-q", str(stack_repo), str(worktree), cwd=stack_repo.parent)
        words = "another repository is there"
    else:
        other = tmp_path / "other"
        git("clone", "-q", str(stack_repo), str(other), cwd=tmp_path)
        git("worktree", "add", "-q", "--detach", str(worktree), cwd=other)
        words = "a worktree of another repository is there"
    before = sorted(p.name for p in worktree.iterdir())
    code, _out, err = _review(stack_repo, store, ["102"], monkeypatch)
    assert code == ExitCode.USAGE
    assert words in err and "ger review --in-place" in err and "git config ger.reviewWorktree" in err
    assert sorted(p.name for p in worktree.iterdir()) == before
    with pytest.raises(ReviewWorktreeBlocked) as refused:
        _post(GerApi(stack_repo, gerrit=store), "/review/open", {"change": 102})
    assert refused.value.reason == "review-worktree-blocked"
    # The same call in this checkout goes through.
    assert (
        _post(GerApi(stack_repo, gerrit=store), "/review/open", {"change": 102, "mode": "branch"})["review"]["mode"]
        == "branch"
    )
