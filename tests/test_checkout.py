# Spec: docu/spec/commands/checkout.md

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_checkout import main as checkout_main
from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.core.gerrit_patchset import changes_ref
from gerrit_workflow_tools.core.git_run import git, git_out
from gerrit_workflow_tools.core.stack import get_stack_snapshot
from tests.change_store import ChangeStore
from tests.cli_gerrit_mocks import build_details_by_change_id, change_info_for_sha, stack_rows_mb_to_head
from tests.conftest import run_cli
from tests.fixtures import _cid


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
    git("config", "gerrit.webUrl", WEB, cwd=stack_repo)
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
    assert "switched to feature: it has change 102 patchset 1" in out
    assert _on(stack_repo) == ("feature", gerrit.shas[3])
    code, out, _ = _checkout(stack_repo, gerrit, ["change:102"], monkeypatch)
    assert code == ExitCode.OK and "already on feature" in out
    assert git_out("branch", "--list", "change-*", cwd=stack_repo) == ""


def test_an_older_patchset_is_updated_and_the_commits_above_it_rebased(
    stack_repo: Path, gerrit: Gerrit, monkeypatch: pytest.MonkeyPatch
) -> None:
    ps2 = gerrit.upload(stack_repo, 2, 2, "test! reworded experiment")
    code, out, err = _checkout(stack_repo, gerrit, ["102", "--update"], monkeypatch)
    assert code == ExitCode.OK, err
    assert "updated feature from patchset 1 to change 102 patchset 2" in out
    assert "rebased the 1 commit(s) above it" in out
    assert git_out("rev-parse", "feature~1", cwd=stack_repo) == ps2
    assert git_out("log", "-1", "--format=%s", "feature", cwd=stack_repo) == "Cleanup after experiment"
    assert _upstream(stack_repo, "feature") == "origin/main"
    # Updated, it is the patchset Gerrit has: the next checkout just says so.
    code, out, _ = _checkout(stack_repo, gerrit, ["102"], monkeypatch)
    assert code == ExitCode.OK and "already on feature: it has change 102 patchset 2" in out


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
    assert code == ExitCode.ATTENTION
    assert "feature has patchset 1 of change 102, Gerrit has patchset 2" in err
    assert "--update" in err and "--no-update" in err
    assert _on(stack_repo) == ("main", git_out("rev-parse", "main", cwd=stack_repo))
    assert git_out("rev-parse", "feature", cwd=stack_repo) == gerrit.shas[3]


@pytest.mark.parametrize(("answer", "updated"), [("", True), ("y", True), ("n", False)])
def test_at_a_terminal_the_update_is_asked_and_defaults_to_yes(
    stack_repo: Path, gerrit: Gerrit, monkeypatch: pytest.MonkeyPatch, answer: str, updated: bool
) -> None:
    ps2 = gerrit.upload(stack_repo, 2, 2, "reworded")
    asked: list[str] = []
    monkeypatch.setattr("gerrit_workflow_tools.cli_checkout.stdin_is_terminal", lambda: True)

    def _answer(prompt: str) -> str:
        asked.append(prompt)
        return answer

    monkeypatch.setattr("builtins.input", _answer)
    assert _checkout(stack_repo, gerrit, ["102"], monkeypatch)[0] == ExitCode.OK
    assert asked == [
        "feature has patchset 1 of change 102. Update it to patchset 2 and rebase the 1 commit(s) above it? [Y/n]: "
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
    assert code == ExitCode.ATTENTION
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
    assert code == ExitCode.ATTENTION and "branch change-102 already exists" in err
    assert git_out("rev-parse", "change-102", cwd=stack_repo) == git_out("rev-parse", "main", cwd=stack_repo)
    assert _checkout(stack_repo, gerrit, ["102", "--force"], monkeypatch)[0] == ExitCode.OK
    assert _on(stack_repo) == ("change-102", gerrit.shas[2])


def test_changes_to_tracked_files_stop_it_before_anything_moves(
    stack_repo: Path, gerrit: Gerrit, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fresh_clone(stack_repo)
    (stack_repo / "README.md").write_text("edited\n", encoding="utf-8")
    code, _, err = _checkout(stack_repo, gerrit, ["102"], monkeypatch)
    assert code == ExitCode.ATTENTION
    assert "changes to tracked files (README.md)" in err and "git stash" in err
    assert git_out("branch", "--list", "change-*", cwd=stack_repo) == ""
    assert (stack_repo / "README.md").read_text(encoding="utf-8") == "edited\n"


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
    assert f"change:200 (testproj~release~{cid}" in err and f"change:201 (testproj~stable~{cid}" in err
    assert _on(stack_repo)[0] == "feature"

    code, out, err = _checkout(stack_repo, gerrit, [f"testproj~stable~{cid}"], monkeypatch)
    assert code == ExitCode.OK, err
    assert "on change-201" in out and _upstream(stack_repo, "change-201") == "origin/stable"
