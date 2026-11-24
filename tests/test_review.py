# Spec: docu/spec/commands/review.md

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from gerrit_workflow_tools.cli_ci import main as ci_main
from gerrit_workflow_tools.cli_comment import main as comment_main
from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_log import main as log_main
from gerrit_workflow_tools.cli_reply import main as reply_main
from gerrit_workflow_tools.cli_review import main as review_main
from gerrit_workflow_tools.cli_show import main as show_main
from gerrit_workflow_tools.core.gerrit_patchset import changes_ref
from gerrit_workflow_tools.core.git_run import git, git_out
from tests.change_store import ChangeStore
from tests.cli_gerrit_mocks import build_details_by_change_id, stack_rows_mb_to_head
from tests.conftest import json_stdout, run_cli


WEB = "https://g.example"
_NO_PROMPT = "no prompt to answer (non-interactive, --json or --ai): pass -y to publish"


@pytest.fixture
def gerrit(stack_repo: Path, tmp_path: Path) -> tuple[ChangeStore, list[str], list[str]]:
    """The stack as changes 100-103 on Gerrit, each one patchset, fetchable from a bare 'origin'.

    Returns the store, the changes' triplets and their commits, base first. The stack's
    branch is reset to main, as when you review someone else's work.
    """
    git("config", "ger.webUrl", WEB, cwd=stack_repo)
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
    assert code == ExitCode.USAGE and "changes to tracked files on top of the staged review (c.txt)" in err
    assert (worktree / "c.txt").read_text(encoding="utf-8") == "my fix\n"
    assert _review(stack_repo, store, ["--done"], monkeypatch)[0] == ExitCode.USAGE


def test_the_advice_for_edits_in_a_staged_review_keeps_them_and_the_review(
    stack_repo: Path, gerrit: tuple[ChangeStore, list[str], list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not git stash: that would take the staged change under review with the edits."""
    store, _keys, shas = gerrit
    _review(stack_repo, store, ["102"], monkeypatch)
    worktree = _worktree(stack_repo)
    (worktree / "c.txt").write_text("my fix\n", encoding="utf-8")
    code, _, err = _review(worktree, store, ["--done"], monkeypatch)
    assert code == ExitCode.USAGE and "git stash" not in err
    advice = err.split("put the change back, `")[1].split("`")[0]
    subprocess.run(advice, shell=True, cwd=worktree, check=True)
    assert git_out("diff", "--cached", "--name-only", cwd=worktree) == "c.txt" and _head(worktree) == shas[1]
    assert _review(worktree, store, ["--done"], monkeypatch)[0] == ExitCode.OK
    git("apply", "review-edits.patch", cwd=worktree)
    assert (worktree / "c.txt").read_text(encoding="utf-8") == "my fix\n"


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
    assert code == ExitCode.REFUSED and "change-102 has 1 commit(s)" in err
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
    monkeypatch.setattr("gerrit_workflow_tools.cli_common.stdin_is_terminal", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "b")
    assert _review(stack_repo, store, ["102"], monkeypatch)[0] == ExitCode.OK
    assert git_out("config", "--local", "ger.reviewCheckout", cwd=stack_repo) == "branch"
    assert not _worktree(stack_repo).exists()


@pytest.mark.parametrize("output", ["--json", "--ai"])
def test_machine_output_never_asks_even_on_a_terminal(
    stack_repo: Path, gerrit: tuple[ChangeStore, list[str], list[str]], monkeypatch: pytest.MonkeyPatch, output: str
) -> None:
    """An agent in a pty passing --ai must not hang on the first-use question (cli_common.may_prompt)."""
    store, _keys, _shas = gerrit
    monkeypatch.setattr("gerrit_workflow_tools.cli_common.stdin_is_terminal", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: pytest.fail("prompted"))
    assert _review(stack_repo, store, ["102", output], monkeypatch)[0] == ExitCode.OK
    assert _worktree(stack_repo).is_dir()
    assert git("config", "--local", "ger.reviewCheckout", cwd=stack_repo, check=False).returncode != 0


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
    assert call.kwargs == {"message": "lgtm", "labels": {"Code-Review": 1, "Verified": -1}, "draft_ids": None}
    assert store.list_drafts(keys[2]) == {}


def test_vote_asks_first_and_never_publishes_without_yes_off_a_terminal(
    stack_repo: Path, gerrit: tuple[ChangeStore, list[str], list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a terminal and without -y: exit 2 and the words of ger reply --publish (cli_common.confirm_publish)."""
    store, _keys, _shas = gerrit
    _review(stack_repo, store, ["102"], monkeypatch)
    worktree = _worktree(stack_repo)
    run_cli(worktree, comment_main, ["c.txt:1", "-m", "nit"], monkeypatch, gerrit=store)
    code, _out, err = _review(stack_repo, store, ["--vote", "CR+2"], monkeypatch)
    reply = run_cli(worktree, reply_main, ["--publish", "--vote", "CR+2"], monkeypatch, gerrit=store)
    assert (code, err.strip()) == (reply[0], reply[2].strip()) == (ExitCode.USAGE, f"error: {_NO_PROMPT}")
    monkeypatch.setattr("gerrit_workflow_tools.cli_common.stdin_is_terminal", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    code, _out, err = _review(stack_repo, store, ["--vote", "CR+2"], monkeypatch)
    assert (code, err.strip().splitlines()[-1]) == (ExitCode.REFUSED, "cancelled")
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
    assert code == ExitCode.REFUSED and "at patchset 2, you reviewed 1" in err
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


def test_naming_a_change_to_review_reads_it_from_gerrit_once(
    stack_repo: Path, gerrit: tuple[ChangeStore, list[str], list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ger review 102`` asks for the change once, and asks again the next time: a patchset is never guessed."""
    store, _keys, _shas = gerrit
    assert _review(stack_repo, store, ["102"], monkeypatch)[0] == ExitCode.OK
    assert len(store.calls_to("get_change")) == 1
    assert _review(stack_repo, store, ["102"], monkeypatch)[0] == ExitCode.OK
    assert len(store.calls_to("get_change")) == 2


def test_a_vote_on_a_named_change_reads_it_once(
    stack_repo: Path, gerrit: tuple[ChangeStore, list[str], list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--vote --change N`` resolves the change and votes: no second read of what was just read."""
    store, _keys, _shas = gerrit
    code, _out, err = _review(stack_repo, store, ["--vote", "CR+1", "--change", "102", "-y"], monkeypatch)
    assert code == ExitCode.OK, err
    assert len(store.calls_to("get_change")) == 1
    assert len(store.calls_to("publish_drafts")) == 1


def test_review_says_what_changed_since_your_last_review(
    stack_repo: Path, gerrit: tuple[ChangeStore, list[str], list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-reviewing patchset 2 after voting on 1: which files moved, and the diff to run."""
    store, keys, shas = gerrit
    git("config", "ger.user", "me", cwd=stack_repo)
    store._accounts[7] = {"_account_id": 7, "username": "me"}  # pylint: disable=protected-access
    store._self_account_id = 7  # pylint: disable=protected-access
    (stack_repo / "late.txt").write_text("added in patchset 2\n", encoding="utf-8")
    git("add", "late.txt", cwd=stack_repo)
    tree = git_out("write-tree", cwd=stack_repo)
    git("reset", "-q", cwd=stack_repo)
    (stack_repo / "late.txt").unlink()
    ps2 = git_out("commit-tree", tree, "-p", f"{shas[2]}^", "-m", "ps2", cwd=stack_repo)
    git("update-ref", changes_ref(102, 2), ps2, cwd=stack_repo)
    store.upload_patchset(keys[2], ps2, 2)
    store.set_messages(keys[2], [{"author": {"_account_id": 7}, "_revision_number": 1, "message": "Code-Review+1"}])
    code, out, err = _review(stack_repo, store, ["102", "--in-place"], monkeypatch)
    assert code == ExitCode.OK, err
    assert "since your review of patchset 1: " in out and "late.txt" in out
    assert f"git diff {shas[2][:12]} {ps2[:12]}" in out


def test_log_show_and_reply_read_the_chain_under_review_while_staged(
    stack_repo: Path, gerrit: tuple[ChangeStore, list[str], list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Staged, HEAD sits detached at the parent: the review's change-<n> branch is the working branch."""
    store, keys, shas = gerrit
    root = {"id": "c-root", "line": 1, "message": "why?", "unresolved": True, "patch_set": 1}
    store.set_comments(keys[2], {"c.txt": [{**root, "updated": "2024-01-01 10:00:00"}]})
    _review(stack_repo, store, ["102"], monkeypatch)
    worktree = _worktree(stack_repo)
    assert _head(worktree) == shas[1]
    code, out, err = run_cli(worktree, log_main, ["--json"], monkeypatch, gerrit=store)
    assert code == ExitCode.OK, err
    assert [c["sha"] for c in json_stdout(out)["commits"]] == shas[:3]
    code, out, err = run_cli(worktree, show_main, ["--json"], monkeypatch, gerrit=store)
    assert code == ExitCode.OK, err
    assert json_stdout(out)["sha"] == shas[2]
    code, out, err = run_cli(worktree, show_main, ["--stack", "--json"], monkeypatch, gerrit=store)
    assert code == ExitCode.OK, err
    assert [c["sha"] for c in json_stdout(out)["commits"]] == shas[:3]
    code, _out, err = run_cli(worktree, ci_main, [], monkeypatch, gerrit=store)
    assert code == ExitCode.OK, err
    for target in (["c-root"], ["c-root", "--change", "102"], [f"{WEB}/c/testproj/+/102/comment/c-root/"]):
        code, _out, err = run_cli(worktree, reply_main, [*target, "-m", "because"], monkeypatch, gerrit=store)
        assert code == ExitCode.OK, (target, err)
    assert [d["message"] for rows in store.list_drafts(keys[2]).values() for d in rows] == ["because"]


def test_a_detached_head_outside_a_review_still_says_check_out_your_branch(
    stack_repo: Path, gerrit: tuple[ChangeStore, list[str], list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _keys, shas = gerrit
    _review(stack_repo, store, ["102"], monkeypatch)
    worktree = _worktree(stack_repo)
    git("switch", "-q", "--detach", shas[0], cwd=worktree)  # HEAD moved off the review's parent
    code, _out, err = run_cli(worktree, log_main, [], monkeypatch, gerrit=store)
    assert code == ExitCode.USAGE and "HEAD is detached and no local branch points at it" in err


def _permit(store: ChangeStore, key: str, **labels: list[str]) -> None:
    """Gerrit's ``permitted_labels`` for the caller on *key* (``Code_Review`` for ``Code-Review``)."""
    row = store._changes[key]  # pylint: disable=protected-access
    row["permitted_labels"] = {label.replace("_", "-"): values for label, values in labels.items()}


def _draft(store: ChangeStore, key: str, message: str) -> None:
    store.set_drafts(key, {"c.txt": [{"id": f"d-{message}", "line": 1, "message": message, "patch_set": 1}]})


def test_a_bare_plus_one_is_a_code_review_vote(
    stack_repo: Path, gerrit: tuple[ChangeStore, list[str], list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _keys, _shas = gerrit
    _review(stack_repo, store, ["102"], monkeypatch)
    code, out, err = _review(stack_repo, store, ["--vote", "+1", "-y"], monkeypatch)
    assert code == ExitCode.OK, err
    assert "published 0 draft(s) and Code-Review+1 on 102" in out
    code, _out, err = run_cli(
        stack_repo, reply_main, ["--publish", "101", "--vote", "-1", "-y"], monkeypatch, gerrit=store
    )
    assert code == ExitCode.OK, err
    assert [call.kwargs["labels"] for call in store.calls_to("publish_drafts")] == [
        {"Code-Review": 1},
        {"Code-Review": -1},
    ]


def test_vote_on_the_chain_lists_each_change_with_its_drafts_and_votes_on_each(
    stack_repo: Path, gerrit: tuple[ChangeStore, list[str], list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """One command finishes a chain: every open change of it, the drafts of each listed first."""
    store, keys, _shas = gerrit
    _draft(store, keys[0], "base nit")
    _draft(store, keys[3], "top nit")
    _review(stack_repo, store, ["101"], monkeypatch)
    code, out, err = _review(stack_repo, store, ["--vote", "+1", "--chain", "--dry-run", "-m", "lgtm"], monkeypatch)
    assert code == ExitCode.OK, err
    assert [line.split(":")[0] for line in out.splitlines() if line.startswith("change ")] == [
        f'change {n} "{subject}"' for n, subject in zip(range(100, 104), _subjects(store, keys), strict=True)
    ]
    assert '  c.txt:1: "base nit"' in out and '  c.txt:1: "top nit"' in out
    assert "  message (on each of 4 changes): lgtm" in out
    assert store.calls_to("publish_drafts") == []
    code, out, err = _review(stack_repo, store, ["--vote", "+1", "--chain", "-y", "-m", "lgtm"], monkeypatch)
    assert code == ExitCode.OK, err
    assert [call.args[0] for call in store.calls_to("publish_drafts")] == keys
    assert "published 1 draft(s) and Code-Review+1 on 103" in out


def test_vote_on_several_named_changes_votes_on_each(
    stack_repo: Path, gerrit: tuple[ChangeStore, list[str], list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, keys, _shas = gerrit
    code, out, err = _review(
        stack_repo, store, ["--vote", "CR+1", "--change", "100", "--change", "101", "-y", "--json"], monkeypatch
    )
    assert code == ExitCode.OK, err
    assert [row["change"] for row in json_stdout(out)["changes"]] == keys[:2]
    assert [call.args[0] for call in store.calls_to("publish_drafts")] == keys[:2]


def test_a_vote_outside_the_label_range_stops_even_a_dry_run(
    stack_repo: Path, gerrit: tuple[ChangeStore, list[str], list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, keys, _shas = gerrit
    for key in keys:
        _permit(store, key, Code_Review=["-1", " 0", "+1"])
    _review(stack_repo, store, ["102"], monkeypatch)
    code, _out, err = _review(stack_repo, store, ["--vote", "CR+3", "--dry-run"], monkeypatch)
    assert code == ExitCode.USAGE
    assert "change 102 does not take Code-Review+3 (you may give -1, 0, +1)" in err


def test_a_chain_vote_skips_the_changes_that_do_not_take_it_and_says_so(
    stack_repo: Path, gerrit: tuple[ChangeStore, list[str], list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, keys, _shas = gerrit
    for key in keys:
        _permit(store, key, Code_Review=["-2", "-1", " 0", "+1", "+2"])
    _permit(store, keys[0], Code_Review=["-1", " 0", "+1"])
    _review(stack_repo, store, ["102"], monkeypatch)
    code, out, err = _review(stack_repo, store, ["--vote", "+2", "--chain", "-y"], monkeypatch)
    assert code == ExitCode.OK, err
    assert "skipped change 100" in out and "Code-Review+2 (you may give -1, 0, +1)" in out
    assert [call.args[0] for call in store.calls_to("publish_drafts")] == keys[1:]


def test_chain_and_change_together_are_a_usage_error(
    stack_repo: Path, gerrit: tuple[ChangeStore, list[str], list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _keys, _shas = gerrit
    assert _review(stack_repo, store, ["--vote", "+1", "--chain", "--change", "100"], monkeypatch)[0] == ExitCode.USAGE
    assert _review(stack_repo, store, ["--chain"], monkeypatch)[0] == ExitCode.USAGE


def _subjects(store: ChangeStore, keys: list[str]) -> list[str]:
    return [store.get_change(key)["subject"] for key in keys]


def test_bottom_starts_at_the_lowest_open_change_of_the_named_chain(
    stack_repo: Path, gerrit: tuple[ChangeStore, list[str], list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """ger inbox names a chain by its top; ger review TOP --bottom starts where review begins."""
    store, _keys, shas = gerrit
    code, out, err = _review(stack_repo, store, ["103", "--bottom", "--json"], monkeypatch)
    assert code == ExitCode.OK, err
    assert (json_stdout(out)["number"], json_stdout(out)["chain"]) == (100, [100, 101, 102, 103])
    assert git_out("rev-parse", "change-100", cwd=_worktree(stack_repo)) == shas[0]
    assert _review(stack_repo, store, ["103", "--bottom", "--patchset", "1"], monkeypatch)[0] == ExitCode.USAGE


def test_review_says_how_to_get_there_and_what_to_run_next(
    stack_repo: Path, gerrit: tuple[ChangeStore, list[str], list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _keys, _shas = gerrit
    _, out, _ = _review(stack_repo, store, ["102"], monkeypatch)
    assert out.splitlines()[-1] == (
        "next: cd ../stack-review · ger show · ger comment PATH:LINE -m … · ger reply ID -m … · "
        "ger review --next · ger review --vote +1"
    )
    _, out, _ = _review(_worktree(stack_repo), store, ["--next"], monkeypatch)  # the top: vote, then done
    assert out.splitlines()[-1] == (
        "next: ger show · ger comment PATH:LINE -m … · ger reply ID -m … · ger review --vote +1 --chain · "
        "ger review --done"
    )


def test_done_warns_about_drafts_left_unpublished_and_still_finishes(
    stack_repo: Path, gerrit: tuple[ChangeStore, list[str], list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, keys, _shas = gerrit
    _review(stack_repo, store, ["101"], monkeypatch)
    _review(stack_repo, store, ["--next"], monkeypatch)
    _draft(store, keys[1], "left behind")
    code, out, err = _review(stack_repo, store, ["--done"], monkeypatch)
    assert code == ExitCode.OK and "done reviewing 102" in out
    assert "warning: 1 unpublished draft on c101: ger reply --publish c101" in err
    # From your own checkout, a publish with nothing on the local stack says where they are.
    git("switch", "-q", "--detach", "main", cwd=stack_repo)
    git("switch", "-q", "-c", "other", "--track", "origin/main", cwd=stack_repo)
    code, out, err = run_cli(stack_repo, reply_main, ["--publish", "--dry-run"], monkeypatch, gerrit=store)
    assert code == ExitCode.OK, err
    assert out.splitlines() == [
        "no drafts to publish on the local stack",
        "  unpublished drafts on c101: ger reply --publish c101",
    ]


def test_comment_from_your_checkout_on_a_file_only_the_reviewed_change_has_goes_to_that_change(
    stack_repo: Path, gerrit: tuple[ChangeStore, list[str], list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """ger review acts on the review worktree from anywhere; so does a comment that can only be meant for it."""
    store, keys, _shas = gerrit
    git("switch", "-q", "-c", "other", "--track", "origin/main", cwd=stack_repo)  # c.txt is not here
    _review(stack_repo, store, ["102"], monkeypatch)
    code, out, err = run_cli(stack_repo, comment_main, ["c.txt:1", "-m", "nit"], monkeypatch, gerrit=store)
    assert code == ExitCode.OK, err
    assert out.splitlines()[:2] == [
        "comment on c102 (under review in ../stack-review)",
        "draft on c.txt:1 (patchset 1)",
    ]
    assert [d["message"] for rows in store.list_drafts(keys[2]).values() for d in rows] == ["nit"]


def test_a_single_change_review_offers_no_walk(
    stack_repo: Path, gerrit: tuple[ChangeStore, list[str], list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, keys, _shas = gerrit
    store.set_related(keys[1], [])  # no relation chain: a change of its own
    _, out, _ = _review(stack_repo, store, ["101"], monkeypatch)
    assert out.splitlines()[-1].endswith("ger reply ID -m … · ger review --vote +1 · ger review --done")
