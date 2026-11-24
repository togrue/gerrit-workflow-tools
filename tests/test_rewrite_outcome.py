"""What a finished rewrite says: the commits it rewrote and dropped, and the next step, from every command alike.

``ger rebase`` (and its ``--continue`` / ``--skip``) and ``ger edit`` end with ger's own lines
after git's "Successfully rebased": a commit dropped is named with the way to get it back, and
nothing is asked. Gerrit's side comes from the cache a ``ger log`` left, never a request.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_edit import main as edit_main
from gerrit_workflow_tools.cli_rebase import main as rebase_main
from gerrit_workflow_tools.core.git_run import git, git_out
from gerrit_workflow_tools.core.rebase_state import rebase_in_progress
from tests.change_store import ChangeStore
from tests.conftest import run_cli
from tests.test_rebasing_stack import _load, _move_upstream_over_c_txt, _store


def _run(repo: Path, main, argv: list[str], store: ChangeStore, monkeypatch: pytest.MonkeyPatch) -> tuple[int, str]:
    code, _out, err = run_cli(repo, main, argv, monkeypatch, catch_sys_exit=True, gerrit=store)
    return code, err


def _pushed(repo: Path) -> ChangeStore:
    """Every commit of the stack pushed, and the cache holding it, as a ``ger log`` leaves it."""
    store = _store(repo)
    _load(repo, store)
    store.calls.clear()
    return store


def _short(repo: Path, rev: str) -> str:
    return git_out("rev-parse", "--short", rev, cwd=repo)


def test_skip_names_the_commit_it_dropped_its_open_change_and_the_way_back(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before, ``--skip`` ended on git's "Successfully rebased" and the pushed commit was gone unsaid."""
    store = _pushed(stack_repo)
    experiment = git_out("rev-parse", "feature~1", cwd=stack_repo)
    _move_upstream_over_c_txt(stack_repo)
    git("rebase", "-q", "origin/main", cwd=stack_repo, check=False)
    assert rebase_in_progress(stack_repo)

    code, err = _run(stack_repo, rebase_main, ["--skip"], store, monkeypatch)

    assert code == 0, err
    lines = err.splitlines()
    dropped = next(i for i, line in enumerate(lines) if line.startswith("dropped "))
    assert re.fullmatch(
        rf"dropped {_short(stack_repo, experiment)} test! temporary experiment; its change \d+ stays open on Gerrit",
        lines[dropped],
    )
    assert lines[dropped + 1] == f"to get it back: git cherry-pick {_short(stack_repo, experiment)}"
    assert lines[dropped + 2 :] == [
        "rebase finished: dropped 1 commit; rewrote all 3 commits, all now ahead of Gerrit",
        "next: `ger push` uploads the stack as it is now",
    ]
    assert store.calls == []  # the cache said it all


def test_drop_and_skip_say_a_dropped_commit_the_same_way(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One wording: ``ger edit --drop`` takes the whole rewrite back by reset, as nothing else changed."""
    store = _pushed(stack_repo)
    tip, experiment = _short(stack_repo, "HEAD"), _short(stack_repo, "HEAD~1")

    code, err = _run(stack_repo, edit_main, ["--drop", "HEAD~1"], store, monkeypatch)

    assert code == 0, err
    assert re.search(
        rf"^dropped {experiment} test! temporary experiment; its change \d+ stays open on Gerrit\n"
        rf"to get it back: git reset --keep {tip}\n"
        r"rebase finished: dropped 1 commit; rewrote 1 of 3 commits, now ahead of Gerrit\n"
        r"next: `ger push` uploads the stack as it is now\n",
        err,
        re.M,
    ), err


def test_a_finished_rebase_says_what_it_rewrote_and_the_next_step(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _pushed(stack_repo)
    git("checkout", "-q", "main", cwd=stack_repo)
    (stack_repo / "u.txt").write_text("upstream\n", encoding="utf-8")
    git("add", "u.txt", cwd=stack_repo)
    git("commit", "-q", "-m", "Upstream moves on", cwd=stack_repo)
    git("update-ref", "refs/remotes/origin/main", "main", cwd=stack_repo)
    git("checkout", "-q", "feature", cwd=stack_repo)
    git("commit", "-q", "--allow-empty", "-m", "Not pushed yet", cwd=stack_repo)

    code, err = _run(stack_repo, rebase_main, ["origin/main"], store, monkeypatch)

    assert code == 0, err
    assert err.splitlines()[-2:] == [
        "rebase finished: rewrote all 5 commits: 4 now ahead of Gerrit, 1 not on Gerrit yet",
        "next: `ger push` uploads the stack as it is now",
    ]


def test_a_rebase_that_changed_nothing_says_so_and_asks_for_nothing_next(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, err = _run(stack_repo, rebase_main, [], _pushed(stack_repo), monkeypatch)

    assert code == 0, err
    assert err.splitlines()[-1] == "rebase finished: nothing changed, the local stack is as it was (4 commits)"


def test_autosquash_says_it_squashed_the_fixup(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _pushed(stack_repo)
    (stack_repo / "a.txt").write_text("fixed\n", encoding="utf-8")
    git("commit", "-q", "-a", "--fixup", "HEAD~3", cwd=stack_repo)

    code, err = _run(stack_repo, rebase_main, ["--autosquash"], store, monkeypatch)

    assert code == 0, err
    assert err.splitlines()[-2:] == [
        "rebase finished: rewrote all 4 commits, all now ahead of Gerrit; squashed 1 fixup",
        "next: `ger push` uploads the stack as it is now",
    ]


def test_without_autosquash_the_next_step_is_to_squash(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The fixup is still there: ``ger push`` would stop at it, so squashing is what comes next."""
    store = _pushed(stack_repo)
    (stack_repo / "a.txt").write_text("fixed\n", encoding="utf-8")
    git("commit", "-q", "-a", "--fixup", "HEAD~3", cwd=stack_repo)
    git("config", "rebase.autosquash", "false", cwd=stack_repo)

    code, err = _run(stack_repo, rebase_main, ["HEAD~5"], store, monkeypatch)

    assert code == 0, err
    assert err.splitlines()[-1] == "next: squash it with `ger rebase --autosquash`"
