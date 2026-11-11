# Spec: docu/spec/commands/review.md

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gerrit_workflow_tools.cli_comment import main as comment_main
from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_review import main as review_main
from gerrit_workflow_tools.core.gerrit_patchset import changes_ref
from gerrit_workflow_tools.core.git_run import git, git_out
from tests.change_store import ChangeStore
from tests.cli_gerrit_mocks import build_details_by_change_id, stack_rows_mb_to_head
from tests.conftest import json_stdout, run_cli


WEB = "https://g.example"


@pytest.fixture
def gerrit(stack_repo: Path, tmp_path: Path) -> tuple[ChangeStore, list[str], list[str]]:
    """The stack as changes 100-103 on Gerrit, each one patchset, fetchable from a bare 'origin'.

    Returns the store, the changes' triplets and their commits, base first. The stack's
    branch is reset to main, as when you review someone else's work.
    """
    git("config", "gerrit.webUrl", WEB, cwd=stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    store = ChangeStore(details, web_base=WEB)
    # 'origin' is the repository itself ('.'), so the change refs only have to exist here.
    for number, row in enumerate(rows, start=100):
        git("update-ref", changes_ref(number, 1), row.sha, cwd=stack_repo)
    keys = list(details)
    related = [{"_change_number": 100 + i, "_revision_number": 1, "status": "NEW"} for i in range(len(rows))]
    for key in keys:
        store.set_related(key, list(reversed(related)))
    return store, keys, [row.sha for row in rows]


def _review(cwd: Path, store: ChangeStore, argv: list[str], monkeypatch: pytest.MonkeyPatch) -> tuple[int, str, str]:
    return run_cli(cwd, review_main, argv, monkeypatch, gerrit=store, catch_sys_exit=True)


def _worktree(repo: Path) -> Path:
    return repo.parent / f"{repo.name}-review"


def _head(cwd: Path) -> str:
    return git_out("rev-parse", "HEAD", cwd=cwd)


def test_review_checks_the_change_out_staged_in_the_review_worktree(
    stack_repo: Path, gerrit: tuple[ChangeStore, list[str], list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, keys, shas = gerrit
    own_head = _head(stack_repo)
    code, out, _ = _review(stack_repo, store, ["102", "--json"], monkeypatch)
    assert code == ExitCode.OK
    state: dict[str, Any] = json_stdout(out)
    worktree = _worktree(stack_repo)
    assert (state["number"], state["key"], state["patch_set"], state["staged"]) == (102, keys[2], 1, True)
    assert Path(state["worktree"]) == worktree
    assert state["chain"] == [100, 101, 102, 103]
    assert state["url"] == f"{WEB}/c/testproj/+/102"
    # Staged: HEAD at the parent, the index holding exactly the change, the branch on the change.
    assert _head(worktree) == shas[1]
    assert git_out("diff", "--cached", "--name-only", cwd=worktree) == "c.txt"
    assert git_out("rev-parse", "refs/heads/change-102", cwd=worktree) == shas[2]
    assert (worktree / "c.txt").is_file()
    # Your own checkout is untouched.
    assert _head(stack_repo) == own_head
    assert git_out("branch", "--show-current", cwd=stack_repo) == "feature"


def test_no_stage_checks_the_branch_out_and_sets_its_upstream(
    stack_repo: Path, gerrit: tuple[ChangeStore, list[str], list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _keys, shas = gerrit
    assert _review(stack_repo, store, ["102", "--no-stage"], monkeypatch)[0] == ExitCode.OK
    worktree = _worktree(stack_repo)
    assert (git_out("branch", "--show-current", cwd=worktree), _head(worktree)) == ("change-102", shas[2])
    assert git_out("rev-parse", "--abbrev-ref", "change-102@{upstream}", cwd=worktree) == "origin/main"


def test_unstage_and_stage_move_head_between_the_change_and_its_parent(
    stack_repo: Path, gerrit: tuple[ChangeStore, list[str], list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _keys, shas = gerrit
    _review(stack_repo, store, ["102"], monkeypatch)
    worktree = _worktree(stack_repo)
    code, out, _ = _review(stack_repo, store, ["--unstage"], monkeypatch)
    assert code == ExitCode.OK and "(staged)" not in out
    assert (git_out("branch", "--show-current", cwd=worktree), _head(worktree)) == ("change-102", shas[2])
    assert _review(worktree, store, ["--stage"], monkeypatch)[0] == ExitCode.OK
    assert _head(worktree) == shas[1]


def test_next_and_prev_walk_the_review_chain_and_stop_at_its_ends(
    stack_repo: Path, gerrit: tuple[ChangeStore, list[str], list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _keys, shas = gerrit
    worktree = _worktree(stack_repo)
    _review(stack_repo, store, ["102"], monkeypatch)
    code, out, _ = _review(stack_repo, store, ["--next", "--json"], monkeypatch)
    assert (code, json_stdout(out)["number"]) == (ExitCode.OK, 103)
    assert git_out("diff", "--cached", "--name-only", cwd=worktree) == "d.txt"
    code, _, err = _review(worktree, store, ["--next"], monkeypatch)
    assert code == ExitCode.NO_EDIT_ATTENTION and "top of its review chain" in err
    code, out, _ = _review(worktree, store, ["--prev", "--json"], monkeypatch)
    assert (code, json_stdout(out)["number"], _head(worktree)) == (ExitCode.OK, 102, shas[1])


def test_your_edits_stop_a_switch_and_are_kept(
    stack_repo: Path, gerrit: tuple[ChangeStore, list[str], list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _keys, _shas = gerrit
    _review(stack_repo, store, ["102"], monkeypatch)
    worktree = _worktree(stack_repo)
    (worktree / "c.txt").write_text("my fix\n", encoding="utf-8")
    code, _, err = _review(stack_repo, store, ["--next"], monkeypatch)
    assert code == ExitCode.ATTENTION and "edits of yours: c.txt" in err
    assert (worktree / "c.txt").read_text(encoding="utf-8") == "my fix\n"
    assert _review(stack_repo, store, ["--done"], monkeypatch)[0] == ExitCode.ATTENTION


def test_a_change_branch_with_your_commits_is_not_moved_without_force(
    stack_repo: Path, gerrit: tuple[ChangeStore, list[str], list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _keys, shas = gerrit
    _review(stack_repo, store, ["102", "--no-stage"], monkeypatch)
    worktree = _worktree(stack_repo)
    git("commit", "-q", "--allow-empty", "-m", "my rework", cwd=worktree)
    mine = _head(worktree)
    _review(stack_repo, store, ["101"], monkeypatch)
    code, _, err = _review(stack_repo, store, ["102"], monkeypatch)
    assert code == ExitCode.ATTENTION and "change-102 has 1 commit(s)" in err
    code, _, err = _review(stack_repo, store, ["102", "--force"], monkeypatch)
    assert code == ExitCode.OK and f"old tip was {mine}" in err
    assert git_out("rev-parse", "change-102", cwd=worktree) == shas[2]


def test_done_deletes_the_review_branches_and_keeps_the_worktree(
    stack_repo: Path, gerrit: tuple[ChangeStore, list[str], list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _keys, _shas = gerrit
    _review(stack_repo, store, ["102"], monkeypatch)
    _review(stack_repo, store, ["--next"], monkeypatch)
    code, out, _ = _review(stack_repo, store, ["--done", "--json"], monkeypatch)
    assert code == ExitCode.OK
    assert sorted(json_stdout(out)["deleted"]) == ["change-102", "change-103"]
    assert git_out("branch", "--list", "change-*", cwd=stack_repo) == ""
    assert _worktree(stack_repo).is_dir()
    assert "no review going on" in _review(stack_repo, store, ["--status"], monkeypatch)[1]


def test_in_place_reviews_in_this_checkout_and_done_goes_back(
    stack_repo: Path, gerrit: tuple[ChangeStore, list[str], list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _keys, shas = gerrit
    own_head = _head(stack_repo)
    assert _review(stack_repo, store, ["102", "--in-place"], monkeypatch)[0] == ExitCode.OK
    assert _head(stack_repo) == shas[1]
    assert not _worktree(stack_repo).exists()
    assert _review(stack_repo, store, ["--done"], monkeypatch)[0] == ExitCode.OK
    assert (git_out("branch", "--show-current", cwd=stack_repo), _head(stack_repo)) == ("feature", own_head)


def test_first_use_asks_where_and_remembers(
    stack_repo: Path, gerrit: tuple[ChangeStore, list[str], list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _keys, _shas = gerrit
    monkeypatch.setattr("gerrit_workflow_tools.cli_review.stdin_is_terminal", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "b")
    assert _review(stack_repo, store, ["102"], monkeypatch)[0] == ExitCode.OK
    assert git_out("config", "--local", "ger.reviewCheckout", cwd=stack_repo) == "branch"
    assert not _worktree(stack_repo).exists()


def test_without_a_terminal_the_review_worktree_is_used_and_nothing_is_saved(
    stack_repo: Path, gerrit: tuple[ChangeStore, list[str], list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _keys, _shas = gerrit
    assert _review(stack_repo, store, ["102"], monkeypatch)[0] == ExitCode.OK
    assert _worktree(stack_repo).is_dir()
    assert git("config", "--local", "ger.reviewCheckout", cwd=stack_repo, check=False).returncode != 0


def test_comment_goes_on_the_change_under_review_while_staged(
    stack_repo: Path, gerrit: tuple[ChangeStore, list[str], list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, keys, _shas = gerrit
    _review(stack_repo, store, ["102"], monkeypatch)
    worktree = _worktree(stack_repo)
    code, out, _ = run_cli(worktree, comment_main, ["c.txt:1", "-m", "nit", "--json"], monkeypatch, gerrit=store)
    assert code == ExitCode.OK
    assert (json_stdout(out)["change"], json_stdout(out)["patch_set"]) == (keys[2], 1)


def test_vote_publishes_your_drafts_with_the_votes(
    stack_repo: Path, gerrit: tuple[ChangeStore, list[str], list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, keys, _shas = gerrit
    _review(stack_repo, store, ["102"], monkeypatch)
    worktree = _worktree(stack_repo)
    run_cli(worktree, comment_main, ["c.txt:1", "-m", "nit"], monkeypatch, gerrit=store)
    code, out, _ = _review(worktree, store, ["--vote", "CR+1", "--vote", "V=-1", "-y", "-m", "lgtm"], monkeypatch)
    assert code == ExitCode.OK and "published 1 draft(s) and Code-Review+1, Verified-1 on 102" in out
    [call] = store.calls_to("publish_drafts")
    assert call.kwargs == {"message": "lgtm", "labels": {"Code-Review": 1, "Verified": -1}}
    assert store.list_drafts(keys[2]) == {}


def test_vote_asks_first_and_never_publishes_without_yes_off_a_terminal(
    stack_repo: Path, gerrit: tuple[ChangeStore, list[str], list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _keys, _shas = gerrit
    _review(stack_repo, store, ["102"], monkeypatch)
    assert _review(stack_repo, store, ["--vote", "CR+2"], monkeypatch)[0] == ExitCode.ATTENTION
    assert _review(stack_repo, store, ["--vote", "CR+2", "--dry-run"], monkeypatch)[0] == ExitCode.OK
    monkeypatch.setenv("GER_NO_PUBLISH", "1")
    assert _review(stack_repo, store, ["--vote", "CR+2", "-y"], monkeypatch)[0] == ExitCode.USAGE
    assert store.calls_to("publish_drafts") == []


def test_vote_refuses_when_gerrit_has_a_newer_patchset(
    stack_repo: Path, gerrit: tuple[ChangeStore, list[str], list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, keys, _shas = gerrit
    _review(stack_repo, store, ["102"], monkeypatch)
    store.upload_patchset(keys[2], "f" * 40, 2)
    code, _, err = _review(stack_repo, store, ["--vote", "CR+1", "-y"], monkeypatch)
    assert code == ExitCode.ATTENTION and "at patchset 2, you reviewed 1" in err
    assert store.calls_to("publish_drafts") == []


@pytest.mark.parametrize(
    "argv",
    [[], ["102", "--next"], ["--vote", "CR+1", "--next"], ["--patchset", "2"], ["-y"], ["--vote", "CR"]],
)
def test_bad_combinations_are_usage_errors(
    stack_repo: Path, gerrit: tuple[ChangeStore, list[str], list[str]], monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    store, _keys, _shas = gerrit
    assert _review(stack_repo, store, argv, monkeypatch)[0] == ExitCode.USAGE


def test_walking_without_a_review_is_a_usage_error(
    stack_repo: Path, gerrit: tuple[ChangeStore, list[str], list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _keys, _shas = gerrit
    code, _, err = _review(stack_repo, store, ["--next"], monkeypatch)
    assert code == ExitCode.USAGE and "no review going on" in err


def test_a_new_patchset_replaces_the_one_the_review_fetched(
    stack_repo: Path, gerrit: tuple[ChangeStore, list[str], list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, keys, shas = gerrit
    assert _review(stack_repo, store, ["102"], monkeypatch)[0] == ExitCode.OK
    tree = git_out("rev-parse", f"{shas[2]}^{{tree}}", cwd=stack_repo)
    ps2 = git_out("commit-tree", tree, "-p", shas[1], "-m", "reworded", cwd=stack_repo)
    git("update-ref", changes_ref(102, 2), ps2, cwd=stack_repo)
    store.upload_patchset(keys[2], ps2, 2)
    code, out, err = _review(stack_repo, store, ["102", "--json"], monkeypatch)
    assert code == ExitCode.OK, err
    assert json_stdout(out)["patch_set"] == 2
    assert git_out("rev-parse", "change-102", cwd=_worktree(stack_repo)) == ps2
