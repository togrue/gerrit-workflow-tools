# Spec: docu/spec/commands/serve.md
"""``ger serve`` for an editor's stack and change views: rebase progress, stash, reword, patchsets."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gerrit_workflow_tools.core.gerrit_patchset import changes_ref
from gerrit_workflow_tools.core.git_run import git, git_out
from gerrit_workflow_tools.core.git_state import git_dir
from gerrit_workflow_tools.core.rebase_state import RebaseProgress, rebase_progress
from gerrit_workflow_tools.core.reorder import ReorderRefusal, UncommittedChanges
from gerrit_workflow_tools.server.api import GerApi
from gerrit_workflow_tools.server.httpd import failure_response
from gerrit_workflow_tools.server.request import BadRequest
from tests.cli_gerrit_mocks import stack_rows_mb_to_head
from tests.test_serve import _gerrit, _get, _post


# --- The stack while a rebase is underway ---------------------------------------------


def test_stack_says_where_a_stopped_rebase_stands(stack_repo: Path) -> None:
    store, _keys, rows = _gerrit(stack_repo)
    api = GerApi(stack_repo, gerrit=store)
    quiet = _get(api, "/stack")
    assert quiet["rebase"] is None and {c["rebase"] for c in quiet["commits"]} == {None}

    edit_first = {"GIT_SEQUENCE_EDITOR": "sed -i 1s/^pick/edit/"}
    git("rebase", "-q", "-i", "HEAD~2", cwd=stack_repo, env=edit_first, check=False)
    stack = _get(api, "/stack")
    # The branch as it was: the two commits below the rebase are not replayed at all.
    assert [c["sha"] for c in stack["commits"]] == [row.sha for row in rows]
    assert [c["rebase"] for c in stack["commits"]] == [None, None, "current", "todo"]
    assert stack["rebase"] == {"applied": True}, "stopped to edit: the commit is there, whole"


def test_a_rebase_stopped_on_a_conflict_has_its_commit_only_partly_applied(stack_repo: Path) -> None:
    state = git_dir(stack_repo)
    assert state is not None
    merge = state / "rebase-merge"
    merge.mkdir()
    (merge / "done").write_text("pick aaaa111 one\nexec make\nfixup -C bbbb222 two\n", encoding="utf-8")
    (merge / "git-rebase-todo").write_text(
        "# comment\npick cccc333 three\nbreak\ndrop dddd444 four\n", encoding="utf-8"
    )
    (state / "REBASE_HEAD").write_text("bbbb222\n", encoding="utf-8")
    progress = rebase_progress(stack_repo)
    assert progress == RebaseProgress(done=("aaaa111",), current="bbbb222", applied=False, todo=("cccc333",))
    assert [progress.state_of(sha) for sha in ("aaaa111f", "bbbb222f", "cccc333f", "dddd444f")] == [
        "done",
        "current",
        "todo",
        None,
    ]
    # Resolved and committed by an edit stop instead: git notes the commit to amend.
    (merge / "amend").write_text("x\n", encoding="utf-8")
    again = rebase_progress(stack_repo)
    assert again is not None and again.applied is True


# --- How a commit relates to Gerrit -----------------------------------------------------


def test_a_commit_ahead_of_a_patchset_this_repository_never_had_has_diverged(stack_repo: Path) -> None:
    store, keys, rows = _gerrit(stack_repo)
    api = GerApi(stack_repo, gerrit=store)
    assert {(c["patchset_status"], c["diverged"]) for c in _get(api, "/stack")["commits"]} == {("active", False)}
    # Your own amend: the pushed patchset is still here, the commit was built on it.
    git("commit", "-q", "--amend", "-m", f"Reworded\n\nChange-Id: {rows[3].change_id}", cwd=stack_repo)
    top = _get(api, "/stack")["commits"][-1]
    assert (top["patchset_status"], top["diverged"]) == ("newer", False)
    # Somebody else uploads a patchset: its commit is not in this repository.
    store.upload_patchset(keys[3], "f" * 40, 2)
    store.add_change({**store.get_change(keys[3]), "updated": "2026-11-09 11:00:00.000000000"})
    top = _get(api, "/stack", refresh="1")["commits"][-1]
    assert (top["patchset_status"], top["diverged"]) == ("newer", True)


def test_a_merge_commit_in_the_stack_is_marked_and_what_is_below_it_stays_put(stack_repo: Path) -> None:
    store, _keys, rows = _gerrit(stack_repo)
    git("checkout", "-q", "-b", "side", "main", cwd=stack_repo)
    (stack_repo / "side.txt").write_text("side\n", encoding="utf-8")
    git("add", "-A", cwd=stack_repo)
    git("commit", "-qm", "On the side", cwd=stack_repo)
    git("checkout", "-q", "feature", cwd=stack_repo)
    git("merge", "-q", "--no-ff", "-m", "Merge side", "side", cwd=stack_repo)
    api = GerApi(stack_repo, gerrit=store)
    stack = _get(api, "/stack")
    # First-parent, as Gerrit relates changes: the merge is a stack commit, what it merged is not.
    assert [c["merge"] for c in stack["commits"]] == [False, False, False, False, True]
    order = [c["sha"] for c in stack["commits"]]
    with pytest.raises(ReorderRefusal, match="at or below the merge commit"):
        _post(api, "/stack/rewrite", {"steps": [order[1], order[0], *order[2:]]})
    assert [row.sha for row in stack_rows_mb_to_head(stack_repo)][:4] == [row.sha for row in rows]
    # A message is another matter: no commit is replayed, the merge keeps both its parents.
    side = git_out("rev-parse", "HEAD^2", cwd=stack_repo)
    _post(api, "/stack/reword", {"rev": rows[0].sha, "message": "Reworded below the merge"})
    after = stack_rows_mb_to_head(stack_repo)
    assert (after[0].subject, after[0].change_id) == ("Reworded below the merge", rows[0].change_id)
    assert git_out("rev-parse", "HEAD^2", cwd=stack_repo) == side
    assert git_out("rev-parse", "HEAD^{tree}", cwd=stack_repo) == git_out(
        "rev-parse", f"{order[-1]}^{{tree}}", cwd=stack_repo
    )


def _merge_then_two_on_top(repo: Path) -> None:
    """``feature`` gets a merge of a side branch, then two commits of its own above it."""
    git("checkout", "-q", "-b", "side", "main", cwd=repo)
    for name in ("side-1", "side-2"):
        (repo / f"{name}.txt").write_text(f"{name}\n", encoding="utf-8")
        git("add", "-A", cwd=repo)
        git("commit", "-qm", f"On the side: {name}", cwd=repo)
    git("checkout", "-q", "feature", cwd=repo)
    git("merge", "-q", "--no-ff", "-m", "Merge side", "side", cwd=repo)
    for name in ("above-1", "above-2"):
        (repo / f"{name}.txt").write_text(f"{name}\n", encoding="utf-8")
        git("add", "-A", cwd=repo)
        git("commit", "-qm", f"Above the merge: {name}", cwd=repo)


def test_commits_above_the_newest_merge_still_move_and_the_merged_side_is_never_listed(stack_repo: Path) -> None:
    store, _keys, rows = _gerrit(stack_repo)
    _merge_then_two_on_top(stack_repo)
    api = GerApi(stack_repo, gerrit=store)
    stack = _get(api, "/stack")
    subjects = [c["summary"] for c in stack["commits"]]
    assert not [s for s in subjects if s.startswith("On the side")], "the merged-in commits are not stack members"
    assert [c["merge"] for c in stack["commits"]] == [False] * 4 + [True, False, False]
    order = [c["sha"] for c in stack["commits"]]
    merge = order[4]
    # Only the pair above the merge can swap; the merge and what is below it are not offered.
    moves = _get(api, "/stack/moves")
    assert [(s["lower"], s["upper"]) for s in moves["swaps"]] == [(order[5], order[6])]

    rewritten = _post(api, "/stack/rewrite", {"steps": [*order[:5], order[6], order[5]]})
    after = [c["summary"] for c in rewritten["commits"]]
    assert after[5:] == ["Above the merge: above-2", "Above the merge: above-1"]
    assert git_out("rev-parse", "HEAD~2", cwd=stack_repo) == merge, "the merge itself was not replayed"
    assert [c["sha"] for c in rewritten["commits"]][:5] == order[:5]

    now = [c["sha"] for c in rewritten["commits"]]
    with pytest.raises(ReorderRefusal, match="cannot be folded into the merge commit"):
        _post(api, "/stack/rewrite", {"steps": [*now[:5], {"sha": now[5], "action": "fixup"}, now[6]]})
    with pytest.raises(ReorderRefusal, match="at or below the merge commit"):
        _post(api, "/stack/rewrite", {"steps": [*now[:4], {"sha": now[4], "action": "drop"}, *now[5:]]})
    assert [row.sha for row in rows] == now[:4]


# --- Rewriting with uncommitted changes -------------------------------------------------


def test_a_rewrite_stashes_uncommitted_changes_only_when_asked_and_puts_them_back(stack_repo: Path) -> None:
    store, _keys, rows = _gerrit(stack_repo)
    api = GerApi(stack_repo, gerrit=store)
    (stack_repo / "a.txt").write_text("file a.txt\nnot committed yet\n", encoding="utf-8")
    steps = [rows[1].sha, rows[0].sha, rows[2].sha, rows[3].sha]
    with pytest.raises(UncommittedChanges) as refused:
        _post(api, "/stack/rewrite", {"steps": steps})
    status, body = failure_response(refused.value)
    assert body["error"]["reason"] == "uncommitted-changes" and status < 500
    assert [row.sha for row in stack_rows_mb_to_head(stack_repo)] == [row.sha for row in rows]

    stack = _post(api, "/stack/rewrite", {"steps": steps, "autostash": True})
    assert [row.subject for row in stack_rows_mb_to_head(stack_repo)] == [rows[i].subject for i in (1, 0, 2, 3)]
    assert stack["stash_kept"] is False
    assert (stack_repo / "a.txt").read_text(encoding="utf-8") == "file a.txt\nnot committed yet\n"
    assert git("stash", "list", cwd=stack_repo).stdout == ""


def test_a_stash_that_no_longer_fits_stays_in_the_stash_and_the_answer_says_so(stack_repo: Path) -> None:
    store, _keys, rows = _gerrit(stack_repo)
    api = GerApi(stack_repo, gerrit=store)
    # An edit to the file of the commit that is dropped: afterwards there is no such file to edit.
    (stack_repo / "d.txt").write_text("file d.txt\nedited\n", encoding="utf-8")
    steps = [*(row.sha for row in rows[:3]), {"sha": rows[3].sha, "action": "drop"}]
    stack = _post(api, "/stack/rewrite", {"steps": steps, "autostash": True})
    assert len(stack["commits"]) == 3 and stack["stash_kept"] is True
    assert "edited" in git("stash", "show", "-p", cwd=stack_repo).stdout


# --- Rewording --------------------------------------------------------------------------


def test_reword_changes_one_message_and_nothing_else(stack_repo: Path) -> None:
    store, _keys, rows = _gerrit(stack_repo)
    api = GerApi(stack_repo, gerrit=store)
    (stack_repo / "a.txt").write_text("file a.txt\nnot committed yet\n", encoding="utf-8")
    trees = [git_out("rev-parse", f"{row.sha}^{{tree}}", cwd=stack_repo) for row in rows]

    # The footer went missing in the editor: the commit stays the same Gerrit change.
    stack = _post(api, "/stack/reword", {"rev": rows[1].sha[:12], "message": "A better subject\n\nAnd why.  \n"})
    after = stack_rows_mb_to_head(stack_repo)
    assert after[0].sha == rows[0].sha, "below the reworded commit nothing is rewritten"
    # Byte for byte: no carriage return sneaks into the commit on Windows.
    raw = subprocess.run(["git", "cat-file", "commit", after[1].sha], cwd=stack_repo, capture_output=True, check=True)
    assert raw.stdout.endswith(f"\n\nA better subject\n\nAnd why.\n\nChange-Id: {rows[1].change_id}\n".encode())
    assert after[1].body == f"A better subject\n\nAnd why.\n\nChange-Id: {rows[1].change_id}"
    assert [c.change_id for c in after] == [row.change_id for row in rows]
    assert [git_out("rev-parse", f"{c.sha}^{{tree}}", cwd=stack_repo) for c in after] == trees
    assert [c["sha"] for c in stack["commits"]] == [c.sha for c in after]
    assert (stack_repo / "a.txt").read_text(encoding="utf-8") == "file a.txt\nnot committed yet\n"
    assert git_out("status", "--porcelain", "--untracked-files=no", cwd=stack_repo).split() == ["M", "a.txt"]

    # The same message again changes nothing; undo takes the reword back.
    same = _post(api, "/stack/reword", {"rev": after[1].sha, "message": after[1].body})
    assert same["head"] == stack["head"] == same["previous_head"]
    _post(api, "/stack/undo", {"head": stack["head"], "previous_head": stack["previous_head"]})
    assert [c.sha for c in stack_rows_mb_to_head(stack_repo)] == [row.sha for row in rows]


def test_reword_refuses_an_empty_message_and_a_commit_outside_the_stack(stack_repo: Path) -> None:
    store, _keys, rows = _gerrit(stack_repo)
    api = GerApi(stack_repo, gerrit=store)
    with pytest.raises(ReorderRefusal, match="message is empty"):
        _post(api, "/stack/reword", {"rev": rows[0].sha, "message": " \n"})
    with pytest.raises(ReorderRefusal, match="not a commit of the local stack"):
        _post(api, "/stack/reword", {"rev": git_out("rev-parse", "main", cwd=stack_repo), "message": "x"})
    assert [c.sha for c in stack_rows_mb_to_head(stack_repo)] == [row.sha for row in rows]


# --- Patchsets and what to compare ------------------------------------------------------


def _two_patchsets(repo: Path) -> tuple[GerApi, str, str, str]:
    """Change 103 with patchset 1 (reviewed by you) and 2, and a local commit ahead of both."""
    store, keys, rows = _gerrit(repo)
    first = rows[3].sha
    (repo / "d.txt").write_text("file d.txt\npatchset two\n", encoding="utf-8")
    git("commit", "-qa", "--amend", "--no-edit", cwd=repo)
    second = git_out("rev-parse", "HEAD", cwd=repo)
    git("update-ref", changes_ref(103, 1), first, cwd=repo)
    git("update-ref", changes_ref(103, 2), second, cwd=repo)
    store.upload_patchset(keys[3], second, 2)
    store.set_messages(keys[3], [{"author": {"_account_id": 1000}, "_revision_number": 1, "message": "Patch Set 1:"}])
    (repo / "d.txt").write_text("file d.txt\npatchset two\nlocal work\n", encoding="utf-8")
    git("commit", "-qa", "--amend", "--no-edit", cwd=repo)
    return GerApi(repo, gerrit=store), first, second, git_out("rev-parse", "HEAD", cwd=repo)


def test_patchsets_lists_what_gerrit_has_and_which_you_reviewed(stack_repo: Path) -> None:
    api, first, second, _local = _two_patchsets(stack_repo)
    answer = _get(api, "/patchsets", rev="change:103")
    assert (answer["number"], answer["current"], answer["reviewed"]) == (103, 2, 1)
    assert [(p["number"], p["commit"]) for p in answer["patchsets"]] == [(1, first), (2, second)]


def test_diff_shows_any_patchset_and_compares_gerrit_with_the_local_commit(stack_repo: Path) -> None:
    api, first, second, local = _two_patchsets(stack_repo)
    # The local commit, with its message; an older patchset on request.
    mine = _get(api, "/diff", rev=local)
    assert (mine["commit"], mine["local_commit"]) == (local, True)
    assert mine["message"].startswith(mine["subject"]) and "Change-Id:" in mine["message"]
    old = _get(api, "/diff", rev="change:103", patchset="1")
    assert (old["commit"], old["local_commit"]) == (first, False)
    assert old["files"] == [{"path": "d.txt", "status": "A", "old_path": None, "insertions": 1, "deletions": 0}]

    # Gerrit against local: the current patchset on the left, your commit on the right.
    gerrit = _get(api, "/diff", rev="change:103", since="current", local=local)
    assert (gerrit["base"], gerrit["commit"], gerrit["local_commit"]) == (second, local, True)
    assert gerrit["since"] == {
        "patch_set": 2,
        "current_patch_set": 2,
        "to_patch_set": None,
        "up_to_date": False,
        "rebased": False,
    }
    assert [(f["path"], f["insertions"], f["deletions"]) for f in gerrit["files"]] == [("d.txt", 1, 0)]

    # Since your review, up to the local commit, and up to a chosen patchset.
    reviewed = _get(api, "/diff", rev="change:103", since="reviewed", local=local)
    assert (reviewed["base"], reviewed["files"][0]["insertions"]) == (first, 2)
    upto = _get(api, "/diff", rev="change:103", since="reviewed", patchset="1")
    assert (upto["since"]["up_to_date"], upto["since"]["to_patch_set"], upto["files"]) == (True, 1, [])
    with pytest.raises(BadRequest, match="local must be"):
        _get(api, "/diff", rev="change:103", since="current", local="--output=x")
