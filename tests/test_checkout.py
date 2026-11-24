# Spec: docu/spec/commands/checkout.md

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gerrit_workflow_tools.cli_checkout import main as checkout_main
from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_log import main as log_main
from gerrit_workflow_tools.cli_push import main as push_main
from gerrit_workflow_tools.core.gerrit_patchset import changes_ref
from gerrit_workflow_tools.core.git_run import git, git_out
from gerrit_workflow_tools.core.stack import get_stack_snapshot
from tests.change_store import ChangeStore
from tests.cli_gerrit_mocks import build_details_by_change_id, change_info_for_sha, stack_rows_mb_to_head
from tests.conftest import run_cli
from tests.fixtures import _cid
from tests.helpers import add_submodule


WEB = "https://g.example"


@dataclass
class Gerrit:
    """The stack as changes 100-103 on a bare 'origin' that only has main and the change refs."""

    store: ChangeStore
    remote: Path
    keys: list[str]
    shas: list[str]
    change_ids: list[str]

    def upload(self, repo: Path, index: int, patch_set: int, subject: str) -> str:
        """A new patchset of change 100+*index* on the same parent and tree, pushed from elsewhere."""
        sha = self.shas[index]
        message = f"{subject}\n\nChange-Id: {self.change_ids[index]}"
        new = git_out("commit-tree", f"{sha}^{{tree}}", "-p", f"{sha}^", "-m", message, cwd=repo)
        git("push", "-q", "origin", f"{new}:{changes_ref(100 + index, patch_set)}", cwd=repo)
        self.store.upload_patchset(self.keys[index], new, patch_set)
        return new


@pytest.fixture
def gerrit(stack_repo: Path, tmp_path: Path) -> Gerrit:
    git("config", "ger.webUrl", WEB, cwd=stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    store = ChangeStore(details, web_base=WEB)
    remote = tmp_path / "gerrit.git"
    git("init", "-q", "--bare", str(remote), cwd=tmp_path)
    git("remote", "set-url", "origin", str(remote), cwd=stack_repo)
    git(
        "push",
        "-q",
        "origin",
        "main",
        *(f"{r.sha}:{changes_ref(100 + i, 1)}" for i, r in enumerate(rows)),
        cwd=stack_repo,
    )
    git("fetch", "-q", "--prune", "origin", cwd=stack_repo)
    return Gerrit(store, remote, list(details), [r.sha for r in rows], [str(r.change_id) for r in rows])


def _checkout(cwd: Path, g: Gerrit, argv: list[str], monkeypatch: pytest.MonkeyPatch) -> tuple[int, str, str]:
    return run_cli(cwd, checkout_main, argv, monkeypatch, gerrit=g.store, catch_sys_exit=True)


def _on(cwd: Path) -> tuple[str, str]:
    return git_out("branch", "--show-current", cwd=cwd), git_out("rev-parse", "HEAD", cwd=cwd)


def _upstream(cwd: Path, branch: str) -> str:
    return git_out("rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}", cwd=cwd)


def _fresh_clone(repo: Path) -> None:
    """Nothing of the stack is local any more: you are on main, as after a clone."""
    git("switch", "-q", "main", cwd=repo)
    git("branch", "-q", "-D", "feature", cwd=repo)


def test_a_change_lands_on_its_own_branch_tracking_its_target_branch(
    stack_repo: Path, gerrit: Gerrit, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fresh_clone(stack_repo)
    code, out, err = _checkout(stack_repo, gerrit, ["102"], monkeypatch)
    assert code == ExitCode.OK, err
    assert 'checked out change 102 patchset 1 "subj" on change-102' in out
    assert "upstream: origin/main" in out and f"{WEB}/c/testproj/+/102" in out
    assert _on(stack_repo) == ("change-102", gerrit.shas[2])
    assert _upstream(stack_repo, "change-102") == "origin/main"
    # The review chain below the change is the local stack there: ger log and ger push see it.
    assert [c.sha for c in get_stack_snapshot(stack_repo).commits] == gerrit.shas[:3]


def test_an_identical_local_version_is_switched_to(
    stack_repo: Path, gerrit: Gerrit, monkeypatch: pytest.MonkeyPatch
) -> None:
    git("switch", "-q", "main", cwd=stack_repo)
    code, out, err = _checkout(stack_repo, gerrit, ["102"], monkeypatch)
    assert code == ExitCode.OK, err
    assert 'switched to feature: change 102 patchset 1 "subj" is 1 commit below HEAD' in out
    assert _on(stack_repo) == ("feature", gerrit.shas[3])
    code, out, _ = _checkout(stack_repo, gerrit, ["change:102"], monkeypatch)
    assert code == ExitCode.OK and 'already on feature: change 102 patchset 1 "subj" is 1 commit below HEAD' in out
    assert _on(stack_repo) == ("feature", gerrit.shas[3]), "HEAD stays where it was"
    code, out, _ = _checkout(stack_repo, gerrit, ["change:103"], monkeypatch)
    assert code == ExitCode.OK and "already on feature: it has change 103 patchset 1" in out
    assert git_out("branch", "--list", "change-*", cwd=stack_repo) == ""


def test_an_older_patchset_is_updated_and_the_commits_above_it_rebased(
    stack_repo: Path, gerrit: Gerrit, monkeypatch: pytest.MonkeyPatch
) -> None:
    ps2 = gerrit.upload(stack_repo, 2, 2, "test! reworded experiment")
    code, out, err = _checkout(stack_repo, gerrit, ["102", "--update"], monkeypatch)
    assert code == ExitCode.OK, err
    assert "updated feature from patchset 1 to change 102 patchset 2" in out
    assert "rebased the 1 commit above it" in out
    assert git_out("rev-parse", "feature~1", cwd=stack_repo) == ps2
    assert git_out("log", "-1", "--format=%s", "feature", cwd=stack_repo) == "Cleanup after experiment"
    assert _upstream(stack_repo, "feature") == "origin/main"
    # Updated, it is the patchset Gerrit has: the next checkout just says so.
    code, out, _ = _checkout(stack_repo, gerrit, ["102"], monkeypatch)
    assert code == ExitCode.OK and "already on feature: change 102 patchset 2" in out
    assert "is 1 commit below HEAD" in out


def _merge_above(repo: Path) -> str:
    """Merge a side branch into feature, the merge itself adding a file no other commit has. The new tip."""
    git("switch", "-q", "-c", "side", "feature~1", cwd=repo)
    (repo / "side.txt").write_text("side\n", encoding="utf-8")
    git("add", "side.txt", cwd=repo)
    git("commit", "-q", "-m", "side", cwd=repo)
    git("switch", "-q", "feature", cwd=repo)
    git("merge", "-q", "--no-ff", "--no-commit", "side", cwd=repo)
    (repo / "merge_only.txt").write_text("resolved by hand\n", encoding="utf-8")
    git("add", "merge_only.txt", cwd=repo)
    git("commit", "-q", "-m", "Merge side", cwd=repo)
    git("branch", "-q", "-D", "side", cwd=repo)
    return git_out("rev-parse", "HEAD", cwd=repo)


@pytest.mark.parametrize("flag", ["--update", "--force"])
def test_a_merge_commit_above_the_change_refuses_the_update_and_changes_nothing(
    stack_repo: Path, gerrit: Gerrit, monkeypatch: pytest.MonkeyPatch, flag: str
) -> None:
    tip = _merge_above(stack_repo)
    ps2 = gerrit.upload(stack_repo, 2, 2, "test! reworded experiment")
    code, out, err = _checkout(stack_repo, gerrit, ["102", flag], monkeypatch)
    assert code == ExitCode.REFUSED, out
    assert '"Merge side"' in err and "is a merge commit" in err and "nothing was changed" in err
    assert f"git rebase --rebase-merges --onto {ps2[:12]}" in err and "-b NAME" in err
    assert _on(stack_repo) == ("feature", tip)
    assert (stack_repo / "merge_only.txt").exists()
    # The way out it names: the patchset on a branch of its own.
    code, _, err = _checkout(stack_repo, gerrit, ["102", "-b", "ps2"], monkeypatch)
    assert code == ExitCode.OK, err
    assert _on(stack_repo) == ("ps2", ps2)


def test_no_update_switches_to_the_older_patchset_unchanged(
    stack_repo: Path, gerrit: Gerrit, monkeypatch: pytest.MonkeyPatch
) -> None:
    gerrit.upload(stack_repo, 2, 2, "reworded")
    git("switch", "-q", "main", cwd=stack_repo)
    code, out, _ = _checkout(stack_repo, gerrit, ["102", "--no-update"], monkeypatch)
    assert code == ExitCode.OK
    assert "switched to feature, left at patchset 1 (Gerrit has patchset 2)" in out
    assert _on(stack_repo) == ("feature", gerrit.shas[3])


def test_without_a_terminal_an_update_is_never_guessed(
    stack_repo: Path, gerrit: Gerrit, monkeypatch: pytest.MonkeyPatch
) -> None:
    gerrit.upload(stack_repo, 2, 2, "reworded")
    git("switch", "-q", "main", cwd=stack_repo)
    code, _, err = _checkout(stack_repo, gerrit, ["102"], monkeypatch)
    assert code == ExitCode.USAGE, "a missing terminal for a required prompt is USAGE, as for push and reply"
    assert err.strip() == (
        "error: no prompt to answer (non-interactive, --json or --ai): "
        "pass --update to update feature from patchset 1 to 2, or --no-update to switch to it as it is"
    )
    assert _on(stack_repo) == ("main", git_out("rev-parse", "main", cwd=stack_repo))
    assert git_out("rev-parse", "feature", cwd=stack_repo) == gerrit.shas[3]


@pytest.mark.parametrize(("answer", "updated"), [("", True), ("y", True), ("n", False)])
def test_at_a_terminal_the_update_is_asked_and_defaults_to_yes(
    stack_repo: Path, gerrit: Gerrit, monkeypatch: pytest.MonkeyPatch, answer: str, updated: bool
) -> None:
    ps2 = gerrit.upload(stack_repo, 2, 2, "reworded")
    asked: list[str] = []
    monkeypatch.setattr("gerrit_workflow_tools.cli_common.stdin_is_terminal", lambda: True)

    def _answer(prompt: str) -> str:
        asked.append(prompt)
        return answer

    monkeypatch.setattr("builtins.input", _answer)
    assert _checkout(stack_repo, gerrit, ["102"], monkeypatch)[0] == ExitCode.OK
    assert asked == [
        "feature has patchset 1 of change 102. Update it to patchset 2 and rebase the 1 commit above it? [Y/n]: "
    ]
    assert (git_out("rev-parse", "feature~1", cwd=stack_repo) == ps2) is updated


def _edited_version(repo: Path, g: Gerrit) -> str:
    """Change 102 reworked on branch 'mine' and never pushed; nothing else of the stack is local."""
    _fresh_clone(repo)
    git("switch", "-q", "-c", "mine", "--track", "origin/main", cwd=repo)
    git("reset", "-q", "--hard", g.shas[1], cwd=repo)
    (repo / "c.txt").write_text("my rework\n", encoding="utf-8")
    git("add", "c.txt", cwd=repo)
    git("commit", "-q", "-m", f"my rework\n\nChange-Id: {g.change_ids[2]}", cwd=repo)
    git("switch", "-q", "main", cwd=repo)
    return git_out("rev-parse", "mine", cwd=repo)


def test_a_local_version_gerrit_never_had_is_not_overwritten(
    stack_repo: Path, gerrit: Gerrit, monkeypatch: pytest.MonkeyPatch
) -> None:
    mine = _edited_version(stack_repo, gerrit)
    code, _, err = _checkout(stack_repo, gerrit, ["102", "--update"], monkeypatch)
    assert code == ExitCode.REFUSED
    assert "mine holds a version of change 102 that Gerrit never had" in err
    assert "--no-update" in err and "-b NAME" in err and "--force" in err
    assert git_out("rev-parse", "mine", cwd=stack_repo) == mine
    assert _on(stack_repo)[0] == "main"


def test_a_local_version_with_edits_offers_its_three_ways_out(
    stack_repo: Path, gerrit: Gerrit, monkeypatch: pytest.MonkeyPatch
) -> None:
    mine = _edited_version(stack_repo, gerrit)
    code, out, _ = _checkout(stack_repo, gerrit, ["102", "--no-update"], monkeypatch)
    assert code == ExitCode.OK and "switched to mine, left as it is" in out
    assert _on(stack_repo) == ("mine", mine)

    code, out, err = _checkout(stack_repo, gerrit, ["102", "-b", "theirs"], monkeypatch)
    assert code == ExitCode.OK and "on theirs" in out
    assert "also has a local version on mine" in err
    assert _on(stack_repo) == ("theirs", gerrit.shas[2]) and _upstream(stack_repo, "theirs") == "origin/main"

    code, _, err = _checkout(stack_repo, gerrit, ["102", "-b", "mine", "--force"], monkeypatch)
    assert code == ExitCode.OK and f"its old tip was {mine}" in err
    assert _on(stack_repo) == ("mine", gerrit.shas[2])


def test_a_branch_name_that_is_taken_is_not_reset_without_force(
    stack_repo: Path, gerrit: Gerrit, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fresh_clone(stack_repo)
    git("branch", "-q", "change-102", "main", cwd=stack_repo)
    code, _, err = _checkout(stack_repo, gerrit, ["102"], monkeypatch)
    assert code == ExitCode.REFUSED and "branch change-102 already exists" in err
    assert git_out("rev-parse", "change-102", cwd=stack_repo) == git_out("rev-parse", "main", cwd=stack_repo)
    assert _checkout(stack_repo, gerrit, ["102", "--force"], monkeypatch)[0] == ExitCode.OK
    assert _on(stack_repo) == ("change-102", gerrit.shas[2])


def test_changes_to_tracked_files_stop_it_before_anything_moves(
    stack_repo: Path, gerrit: Gerrit, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fresh_clone(stack_repo)
    (stack_repo / "README.md").write_text("edited\n", encoding="utf-8")
    code, _, err = _checkout(stack_repo, gerrit, ["102"], monkeypatch)
    assert code == ExitCode.USAGE  # a state you fix, then rerun: as for ger edit
    assert "changes to tracked files (README.md)" in err and "git stash" in err
    assert git_out("branch", "--list", "change-*", cwd=stack_repo) == ""
    assert (stack_repo / "README.md").read_text(encoding="utf-8") == "edited\n"


def test_a_submodule_at_another_commit_or_with_edits_inside_does_not_stop_an_update(
    stack_repo: Path, gerrit: Gerrit, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#41: switching and rebasing leave a submodule's own state alone, so it is not in the way."""
    sub = add_submodule(stack_repo, tmp_path)  # a commit above the stack: rebased with it
    gerrit.upload(stack_repo, 2, 2, "test! reworded experiment")
    git("checkout", "-q", "HEAD~1", cwd=sub)
    (sub / "a.txt").write_text("dirty\n", encoding="utf-8")
    (sub / "untracked.txt").write_text("new\n", encoding="utf-8")
    sub_head = git_out("rev-parse", "HEAD", cwd=sub)

    code, out, err = _checkout(stack_repo, gerrit, ["102", "--update"], monkeypatch)
    assert code == ExitCode.OK, err
    assert "updated feature from patchset 1 to change 102 patchset 2" in out
    assert "rebased the 2 commits above it" in out
    assert git_out("rev-parse", "HEAD", cwd=sub) == sub_head
    assert (sub / "a.txt").read_text(encoding="utf-8") == "dirty\n"
    assert (sub / "untracked.txt").read_text(encoding="utf-8") == "new\n"


def test_a_submodule_staged_at_another_commit_stops_it_naming_the_submodule(
    stack_repo: Path, gerrit: Gerrit, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``git stash`` does not save a staged submodule commit, and the rebase would stop on it."""
    sub = add_submodule(stack_repo, tmp_path)
    gerrit.upload(stack_repo, 2, 2, "test! reworded experiment")
    git("checkout", "-q", "HEAD~1", cwd=sub)
    git("add", "lib", cwd=stack_repo)
    before = _on(stack_repo)

    code, _, err = _checkout(stack_repo, gerrit, ["102", "--update"], monkeypatch)
    assert code == ExitCode.USAGE
    assert err.strip() == (
        "error: the submodule commit staged for lib is not committed; "
        "commit it or `git restore --staged lib`, then run ger checkout again"
    )
    assert _on(stack_repo) == before
    git("restore", "--staged", "lib", cwd=stack_repo)
    assert _checkout(stack_repo, gerrit, ["102", "--update"], monkeypatch)[0] == ExitCode.OK


def test_patchset_picks_an_older_one(stack_repo: Path, gerrit: Gerrit, monkeypatch: pytest.MonkeyPatch) -> None:
    gerrit.upload(stack_repo, 2, 2, "reworded")
    _fresh_clone(stack_repo)
    code, out, _ = _checkout(stack_repo, gerrit, ["102", "--patchset", "1"], monkeypatch)
    assert code == ExitCode.OK and "change 102 patchset 1" in out
    assert _on(stack_repo) == ("change-102", gerrit.shas[2])
    assert _checkout(stack_repo, gerrit, ["102", "--patchset", "7"], monkeypatch)[0] == ExitCode.NOT_FOUND


def _same_change_id_on_two_branches(repo: Path, g: Gerrit) -> str:
    """One Change-Id as change 200 on 'release' and 201 on 'stable', which exist on the remote only."""
    cid = _cid("e")
    main = git_out("rev-parse", "main", cwd=repo)
    for number, branch in ((200, "release"), (201, "stable")):
        tree = git_out("rev-parse", f"{main}^{{tree}}", cwd=repo)
        sha = git_out("commit-tree", tree, "-p", main, "-m", f"backport to {branch}\n\nChange-Id: {cid}", cwd=repo)
        git("push", "-q", "origin", f"{main}:refs/heads/{branch}", f"{sha}:{changes_ref(number, 1)}", cwd=repo)
        g.store.add_change(change_info_for_sha(sha, cid, branch=branch, number=number))
    return cid


def test_a_change_id_on_two_other_branches_is_ambiguous_and_lists_them(
    stack_repo: Path, gerrit: Gerrit, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid = _same_change_id_on_two_branches(stack_repo, gerrit)
    code, _, err = _checkout(stack_repo, gerrit, [cid], monkeypatch)
    assert code == ExitCode.AMBIGUOUS
    assert f"c200 (testproj~release~{cid}" in err and f"c201 (testproj~stable~{cid}" in err
    assert _on(stack_repo)[0] == "feature"

    code, out, err = _checkout(stack_repo, gerrit, [f"testproj~stable~{cid}"], monkeypatch)
    assert code == ExitCode.OK, err
    assert "on change-201" in out and _upstream(stack_repo, "change-201") == "origin/stable"


def test_bookkeeping_branches_are_not_local_versions(
    stack_repo: Path, gerrit: Gerrit, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``lastPush/<branch>`` is ger's marker: never named as a local version, never switched onto."""
    git("branch", "-q", "lastPush/feature", "feature", cwd=stack_repo)
    git("switch", "-q", "main", cwd=stack_repo)
    code, out, err = _checkout(stack_repo, gerrit, ["102"], monkeypatch)
    assert code == ExitCode.OK and "switched to feature" in out
    assert "lastPush" not in out + err

    git("switch", "-q", "main", cwd=stack_repo)
    git("branch", "-q", "-D", "feature", cwd=stack_repo)
    code, out, err = _checkout(stack_repo, gerrit, ["102"], monkeypatch)
    assert code == ExitCode.OK, err
    assert _on(stack_repo) == ("change-102", gerrit.shas[2]) and "lastPush" not in out + err


def test_back_to_a_change_after_checking_out_the_one_stacked_on_it(
    stack_repo: Path, gerrit: Gerrit, monkeypatch: pytest.MonkeyPatch
) -> None:
    """change-101 then change-102 (which holds 101 too): ``ger checkout 101`` goes back, no question."""
    _fresh_clone(stack_repo)
    for number in ("101", "102", "101"):
        code, _out, err = _checkout(stack_repo, gerrit, [number], monkeypatch)
        assert code == ExitCode.OK, err
    assert _on(stack_repo) == ("change-101", gerrit.shas[1])
    code, _out, err = _checkout(stack_repo, gerrit, ["102"], monkeypatch)
    assert code == ExitCode.OK, err
    assert _on(stack_repo) == ("change-102", gerrit.shas[2])


def test_moving_to_an_older_patchset_says_moved(
    stack_repo: Path, gerrit: Gerrit, monkeypatch: pytest.MonkeyPatch
) -> None:
    gerrit.upload(stack_repo, 2, 2, "test! reworded experiment")
    assert _checkout(stack_repo, gerrit, ["102", "--update"], monkeypatch)[0] == ExitCode.OK
    code, out, err = _checkout(stack_repo, gerrit, ["102", "--update", "--patchset", "1"], monkeypatch)
    assert code == ExitCode.OK, err
    assert "moved feature from patchset 2 to change 102 patchset 1" in out


@pytest.mark.parametrize("private", [False, True])
def test_a_patchset_ref_the_remote_does_not_serve_says_what_is_likely_wrong(
    private: bool, stack_repo: Path, gerrit: Gerrit, monkeypatch: pytest.MonkeyPatch
) -> None:
    """git's own words, then one line: a private change's ref needs a signed-in remote; else check ger.remote."""
    _fresh_clone(stack_repo)
    git("update-ref", "-d", changes_ref(102, 1), cwd=gerrit.remote)
    gerrit.store.set_private(gerrit.keys[2], private)

    code, _out, err = _checkout(stack_repo, gerrit, ["102"], monkeypatch)

    assert code == ExitCode.GIT
    first, hint = err.strip().splitlines()[-2:]
    assert first.startswith("error: git fetch") and f"couldn't find remote ref {changes_ref(102, 1)}" in first
    generic = f"remote 'origin' does not have {changes_ref(102, 1)}: check that ger.remote names the Gerrit server"
    private_hint = "change 102 is private: Gerrit does not serve a private change's refs to remote 'origin'"
    assert hint.startswith(private_hint if private else generic)


def test_help_states_which_branch_a_change_on_a_branch_goes_to(
    stack_repo: Path, gerrit: Gerrit, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, out, _err = _checkout(stack_repo, gerrit, ["--help"], monkeypatch)
    assert code == 0
    assert "a branch whose tip is the change wins" in " ".join(out.split())
    assert "commits below HEAD" in " ".join(out.split())


def _diverged(repo: Path, g: Gerrit, *, theirs: str, mine: str) -> tuple[str, str]:
    """Change 102 amended here on 'mine' (adds *mine*), while somebody uploaded patchset 2 (adds *theirs*)."""
    ps1 = g.shas[2]
    _fresh_clone(repo)
    git("switch", "-q", "-c", "other", ps1, cwd=repo)
    name, _, text = theirs.partition("=")
    (repo / name).write_text(text + "\n", encoding="utf-8")
    git("add", name, cwd=repo)
    git("commit", "-q", "--amend", "-m", f"their take\n\nChange-Id: {g.change_ids[2]}", cwd=repo)
    ps2 = git_out("rev-parse", "HEAD", cwd=repo)
    git("push", "-q", "origin", f"{ps2}:{changes_ref(102, 2)}", cwd=repo)
    g.store.upload_patchset(g.keys[2], ps2, 2)
    git("switch", "-q", "-c", "mine", "--track", "origin/main", cwd=repo)
    git("reset", "-q", "--hard", ps1, cwd=repo)
    name, _, text = mine.partition("=")
    (repo / name).write_text(text + "\n", encoding="utf-8")
    git("add", name, cwd=repo)
    git("commit", "-q", "--amend", "-m", f"my take\n\nChange-Id: {g.change_ids[2]}", cwd=repo)
    git("branch", "-q", "-D", "other", cwd=repo)
    return ps2, git_out("rev-parse", "HEAD", cwd=repo)


def test_update_merges_a_patchset_somebody_else_uploaded_into_your_amend(
    stack_repo: Path, gerrit: Gerrit, monkeypatch: pytest.MonkeyPatch
) -> None:
    ps2, _mine = _diverged(stack_repo, gerrit, theirs="theirs.txt=bob", mine="mine.txt=me")
    code, out, err = _checkout(stack_repo, gerrit, ["102", "--update"], monkeypatch)
    assert code == ExitCode.OK, err
    assert "merged your version on mine into change 102 patchset 2" in out
    assert "your edits against patchset 1" in out
    head = git_out("rev-parse", "HEAD", cwd=stack_repo)
    assert git_out("show", f"{head}:theirs.txt", cwd=stack_repo) == "bob"
    assert git_out("show", f"{head}:mine.txt", cwd=stack_repo) == "me"
    assert git_out("rev-parse", f"{head}^", cwd=stack_repo) == git_out("rev-parse", f"{ps2}^", cwd=stack_repo)
    assert git_out("log", "-1", "--format=%s", head, cwd=stack_repo) == "my take"


def test_update_refuses_a_merge_that_conflicts_and_changes_nothing(
    stack_repo: Path, gerrit: Gerrit, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ps2, mine = _diverged(stack_repo, gerrit, theirs="same.txt=bob", mine="same.txt=me")
    code, _, err = _checkout(stack_repo, gerrit, ["102", "--update"], monkeypatch)
    assert code == ExitCode.REFUSED
    assert "change the same lines (same.txt); nothing was changed" in err
    assert git_out("rev-parse", "mine", cwd=stack_repo) == mine


def _log_reasons(repo: Path, g: Gerrit, monkeypatch: pytest.MonkeyPatch, change_id: str) -> list[str]:
    code, out, err = run_cli(repo, log_main, ["--json"], monkeypatch, gerrit=g.store)
    assert code == ExitCode.OK, err
    return next(c["attention_reasons"] for c in json.loads(out)["commits"] if c["change_id"] == change_id)


def _push_refusal(repo: Path, g: Gerrit, monkeypatch: pytest.MonkeyPatch) -> tuple[int, str]:
    sent = MagicMock(return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""))
    monkeypatch.setattr("gerrit_workflow_tools.core.push.run_git_push", sent)
    code, _out, err = run_cli(repo, push_main, ["-y"], monkeypatch, gerrit=g.store)
    assert sent.called == (code == ExitCode.OK)
    return code, err


def test_a_refused_update_leaves_the_change_diverged_in_log(
    stack_repo: Path, gerrit: Gerrit, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refused merge fetched patchset 2, but fetching it is not building on it (ADR-0015)."""
    ps2, mine = _diverged(stack_repo, gerrit, theirs="same.txt=bob", mine="same.txt=me")
    assert "diverged" in _log_reasons(stack_repo, gerrit, monkeypatch, gerrit.change_ids[2])
    code, _, err = _checkout(stack_repo, gerrit, ["102", "--update"], monkeypatch)
    assert code == ExitCode.REFUSED and "nothing was changed" in err
    assert git_out("cat-file", "-t", ps2, cwd=stack_repo) == "commit", "the refused update fetched it"
    assert git_out("rev-parse", "mine", cwd=stack_repo) == mine
    assert "diverged" in _log_reasons(stack_repo, gerrit, monkeypatch, gerrit.change_ids[2])


def test_a_refused_update_leaves_the_push_refused(
    stack_repo: Path, gerrit: Gerrit, monkeypatch: pytest.MonkeyPatch
) -> None:
    _diverged(stack_repo, gerrit, theirs="same.txt=bob", mine="same.txt=me")
    assert _checkout(stack_repo, gerrit, ["102", "--update"], monkeypatch)[0] == ExitCode.REFUSED
    code, err = _push_refusal(stack_repo, gerrit, monkeypatch)
    assert code == ExitCode.REFUSED and "would replace patchset 2 of change 102" in err


def test_a_fetched_patchset_is_not_one_you_built_on(
    stack_repo: Path, gerrit: Gerrit, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`git fetch refs/changes/…`, or `ger review` putting it on a branch of its own: still diverged."""
    _diverged(stack_repo, gerrit, theirs="theirs.txt=bob", mine="mine.txt=me")
    git("fetch", "-q", "origin", changes_ref(102, 2), cwd=stack_repo)
    git("branch", "-q", "change-102", "FETCH_HEAD", cwd=stack_repo)
    assert "diverged" in _log_reasons(stack_repo, gerrit, monkeypatch, gerrit.change_ids[2])


def test_a_merged_patchset_counts_as_built_on_and_is_no_longer_diverged(
    stack_repo: Path, gerrit: Gerrit, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The merge sits where the patchset sits, not on it: ger records that it took it over."""
    ps2, _mine = _diverged(stack_repo, gerrit, theirs="theirs.txt=bob", mine="mine.txt=me")
    code, _, err = _checkout(stack_repo, gerrit, ["102", "--update"], monkeypatch)
    assert code == ExitCode.OK, err
    head = git_out("rev-parse", "HEAD", cwd=stack_repo)
    assert git_out("rev-parse", f"refs/ger/taken/{ps2}", cwd=stack_repo) == head
    reasons = _log_reasons(stack_repo, gerrit, monkeypatch, gerrit.change_ids[2])
    assert "ahead-of-gerrit" in reasons and "diverged" not in reasons
    # Amended again since: still built on it, the amended commit's history holds the merge.
    git("commit", "-q", "--amend", "-m", f"my take, again\n\nChange-Id: {gerrit.change_ids[2]}", cwd=stack_repo)
    assert "diverged" not in _log_reasons(stack_repo, gerrit, monkeypatch, gerrit.change_ids[2])
    assert _push_refusal(stack_repo, gerrit, monkeypatch)[0] == ExitCode.OK


def test_a_conflicting_merge_says_how_to_merge_by_hand(
    stack_repo: Path, gerrit: Gerrit, monkeypatch: pytest.MonkeyPatch
) -> None:
    ps2, mine = _diverged(stack_repo, gerrit, theirs="same.txt=bob", mine="same.txt=me")
    _code, _, err = _checkout(stack_repo, gerrit, ["102", "--update"], monkeypatch)
    ps1 = gerrit.shas[2]
    assert f"patchset 2 of change 102 ({ps2[:12]})" in err
    assert f"see where: git diff {mine[:7]} {ps2[:12]} -- same.txt" in err
    assert "ger checkout change:102 --force" in err
    assert f"git diff {ps1[:12]} {mine[:7]} | git apply -3" in err
    assert "ger push --force" in err


def test_yes_answers_the_update_question(stack_repo: Path, gerrit: Gerrit, monkeypatch: pytest.MonkeyPatch) -> None:
    ps2 = gerrit.upload(stack_repo, 2, 2, "test! reworded experiment")
    code, out, err = _checkout(stack_repo, gerrit, ["102", "-y"], monkeypatch)
    assert code == ExitCode.OK, err
    assert "updated feature from patchset 1 to change 102 patchset 2" in out
    assert git_out("rev-parse", "feature~1", cwd=stack_repo) == ps2


def _web_edited_bottom(repo: Path, g: Gerrit, *, rebased_by_gerrit: bool = True) -> tuple[str, str]:
    """s06: change 102 edited in Gerrit's web editor (patchset 2), and change 103 above it rebased by
    Gerrit onto that (patchset 2), or left on patchset 1 of 102. Locally both are still patchset 1."""
    bottom, top = g.shas[2], g.shas[3]
    git("switch", "-q", "--detach", bottom, cwd=repo)
    (repo / "web.txt").write_text("edited on the web\n", encoding="utf-8")
    git("add", "web.txt", cwd=repo)
    git("commit", "-q", "--amend", "--no-edit", cwd=repo)
    bottom2 = git_out("rev-parse", "HEAD", cwd=repo)
    git("cherry-pick", top, cwd=repo)
    top2 = (
        git_out("rev-parse", "HEAD", cwd=repo)
        if rebased_by_gerrit
        else git_out(
            "commit-tree", f"{top}^{{tree}}", "-p", bottom, "-m", f"rebased\n\nChange-Id: {g.change_ids[3]}", cwd=repo
        )
    )
    git("push", "-q", "origin", f"{bottom2}:{changes_ref(102, 2)}", f"{top2}:{changes_ref(103, 2)}", cwd=repo)
    g.store.upload_patchset(g.keys[2], bottom2, 2, parent=g.shas[1])
    g.store.upload_patchset(g.keys[3], top2, 2, parent=bottom2 if rebased_by_gerrit else bottom)
    git("switch", "-q", "feature", cwd=repo)
    return bottom2, top2


def _statuses(repo: Path, g: Gerrit, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    code, out, err = run_cli(repo, log_main, ["--json"], monkeypatch, gerrit=g.store)
    assert code == ExitCode.OK, err
    return [c["patchset_status"] for c in json.loads(out)["commits"]]


def _nothing_to_push(repo: Path, g: Gerrit, monkeypatch: pytest.MonkeyPatch) -> bool:
    code, out, err = run_cli(repo, push_main, ["--dry-run"], monkeypatch, gerrit=g.store)
    assert code == ExitCode.OK, err
    return "nothing to push" in out


def test_an_update_takes_the_commits_above_as_gerrit_rebased_them(
    stack_repo: Path, gerrit: Gerrit, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Following `next:` on s06 leaves the stack as Gerrit has it: no rebase of yours to push as a new patchset."""
    bottom2, top2 = _web_edited_bottom(stack_repo, gerrit)
    assert _statuses(stack_repo, gerrit, monkeypatch) == ["active", "active", "outdated", "outdated"]
    code, out, err = _checkout(stack_repo, gerrit, ["102", "--update"], monkeypatch)
    assert code == ExitCode.OK, err
    assert "updated feature from patchset 1 to change 102 patchset 2" in out
    assert "took change 103 patchset 2 above it as Gerrit has it" in out and "rebased" not in out
    assert git_out("rev-parse", "feature", "feature~1", cwd=stack_repo).split() == [top2, bottom2]
    assert _statuses(stack_repo, gerrit, monkeypatch) == ["active"] * 4
    assert _nothing_to_push(stack_repo, gerrit, monkeypatch)


def test_a_commit_above_that_gerrit_left_on_the_old_patchset_is_rebased(
    stack_repo: Path, gerrit: Gerrit, monkeypatch: pytest.MonkeyPatch
) -> None:
    bottom2, top2 = _web_edited_bottom(stack_repo, gerrit, rebased_by_gerrit=False)
    code, out, err = _checkout(stack_repo, gerrit, ["102", "--update"], monkeypatch)
    assert code == ExitCode.OK, err
    assert "rebased the 1 commit above it" in out and "took" not in out
    assert git_out("rev-parse", "feature~1", cwd=stack_repo) == bottom2
    assert git_out("rev-parse", "feature", cwd=stack_repo) != top2


def test_update_without_a_change_brings_the_whole_stack_up_to_date(
    stack_repo: Path, gerrit: Gerrit, monkeypatch: pytest.MonkeyPatch
) -> None:
    bottom2, top2 = _web_edited_bottom(stack_repo, gerrit)
    code, out, err = _checkout(stack_repo, gerrit, ["--update"], monkeypatch)
    assert code == ExitCode.OK, err
    assert "updated feature from patchset 1 to change 102 patchset 2" in out
    assert "took change 103 patchset 2 above it as Gerrit has it" in out
    assert git_out("rev-parse", "feature", "feature~1", cwd=stack_repo).split() == [top2, bottom2]
    assert _nothing_to_push(stack_repo, gerrit, monkeypatch)
    code, out, _ = _checkout(stack_repo, gerrit, ["--update"], monkeypatch)
    assert code == ExitCode.OK and "nothing to update: no commit of feature is outdated" in out


def test_update_without_a_change_leaves_a_change_with_another_upload_to_merge(
    stack_repo: Path, gerrit: Gerrit, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gerrit's 103 has a patchset 2 on 102's patchset 1: yours is rebased onto 102's new patchset, and
    stays diverged from that upload until you merge it (`ger checkout change:103 --update`)."""
    bottom2, _top2 = _web_edited_bottom(stack_repo, gerrit, rebased_by_gerrit=False)
    code, _out, err = _checkout(stack_repo, gerrit, ["--update"], monkeypatch)
    assert code == ExitCode.OK, err
    assert git_out("rev-parse", "feature~1", cwd=stack_repo) == bottom2
    assert _statuses(stack_repo, gerrit, monkeypatch) == ["active", "active", "active", "newer"]
    assert "diverged" in _log_reasons(stack_repo, gerrit, monkeypatch, gerrit.change_ids[3])


@pytest.mark.parametrize("argv", [[], ["--no-update"], ["--update", "-b", "x"], ["--update", "--force"]])
def test_without_a_change_only_update_is_accepted(
    stack_repo: Path, gerrit: Gerrit, monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    code, _, err = _checkout(stack_repo, gerrit, argv, monkeypatch)
    assert code == ExitCode.USAGE
    assert "name the change to check out" in err


def test_a_merge_that_adds_nothing_takes_gerrits_patchset_itself(
    stack_repo: Path, gerrit: Gerrit, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Your 103 rebased by hand onto 102's new patchset, as Gerrit did: the merge is Gerrit's commit."""
    bottom2, top2 = _web_edited_bottom(stack_repo, gerrit)
    later = {"GIT_COMMITTER_DATE": "2030-01-01T00:00:00"}
    git("rebase", "-q", "--onto", bottom2, gerrit.shas[2], "feature", cwd=stack_repo, env=later)
    mine = git_out("rev-parse", "feature", cwd=stack_repo)
    assert mine != top2
    code, out, err = _checkout(stack_repo, gerrit, ["103", "--update"], monkeypatch)
    assert code == ExitCode.OK, err
    assert 'your version on feature has nothing change 103 patchset 2 "subj" lacks: took it as Gerrit has it' in out
    assert git_out("rev-parse", "feature", cwd=stack_repo) == top2
    assert git_out("for-each-ref", "refs/ger/taken/", cwd=stack_repo) == "", "Gerrit's own commit: nothing to record"
    assert _nothing_to_push(stack_repo, gerrit, monkeypatch)
