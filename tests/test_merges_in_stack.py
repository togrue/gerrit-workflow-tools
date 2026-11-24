"""A local stack that holds a merge commit (#93).

ger does not replay merges: a rewrite that would have to replay one refuses with nothing
changed, the commits above the newest merge stay rewritable, and the push preview says which
commits a merge brings in that ``git push`` uploads besides the ones it lists.

Two stacks, both on ``main``: the **upstream merged in** (``A → B → Merge main → C``, ``main``
having gained ``U1``) and a **side branch merged in** (``local → Merge side(S1, S2) → C``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_edit import main as edit_main, main_reword as reword_main
from gerrit_workflow_tools.cli_fix import main as fix_main
from gerrit_workflow_tools.cli_push import main as push_main
from gerrit_workflow_tools.cli_rebase import main as rebase_main
from gerrit_workflow_tools.core.git_run import git, git_out
from tests.conftest import run_cli
from tests.fixtures import configure_gerrit_target


def _commit(repo: Path, name: str, change: str) -> str:
    (repo / f"{name}.txt").write_text(f"{name}\n", encoding="utf-8")
    git("add", f"{name}.txt", cwd=repo)
    git("commit", "-q", "-m", f"{name}\n\nChange-Id: I{change * 40}", cwd=repo)
    return git_out("rev-parse", "HEAD", cwd=repo)


def _only_main_fetched(repo: Path) -> None:
    """The fixtures fetch their own branches from ``origin``; Gerrit would have only ``main``."""
    for ref in git_out("for-each-ref", "--format=%(refname)", "refs/remotes/origin/", cwd=repo).split():
        if ref != "refs/remotes/origin/main":
            git("update-ref", "-d", ref, cwd=repo)


@pytest.fixture
def upstream_merged_in(tmp_path: Path) -> Path:
    """``A → B → Merge main → C`` on ``feature``, tracking ``origin/main``, which has ``U1`` too."""
    repo = tmp_path / "upstream"
    repo.mkdir()
    git("init", "-q", "-b", "main", cwd=repo)
    _commit(repo, "base", "0")
    git("checkout", "-q", "-b", "feature", cwd=repo)
    _commit(repo, "A", "a")
    _commit(repo, "B", "b")
    git("checkout", "-q", "main", cwd=repo)
    _commit(repo, "U1", "1")
    git("checkout", "-q", "feature", cwd=repo)
    git("merge", "-q", "--no-ff", "-m", f"Merge main\n\nChange-Id: I{'c' * 40}", "main", cwd=repo)
    _commit(repo, "C", "d")
    configure_gerrit_target(repo, "main")
    git("config", "ger.project", "testproj", cwd=repo)
    git("config", "ger.webUrl", "https://g.example", cwd=repo)
    _only_main_fetched(repo)
    return repo


@pytest.fixture
def side_merged_in(merged_side_branch_repo: Path) -> Path:
    """``local → Merge side branch → C``; ``S1`` and ``S2`` are on no remote-tracking branch."""
    repo = merged_side_branch_repo
    _commit(repo, "C", "d")
    _only_main_fetched(repo)
    return repo


@pytest.fixture(params=["upstream_merged_in", "side_merged_in"])
def merged(request: pytest.FixtureRequest) -> Path:
    return request.getfixturevalue(request.param)


def _merge(repo: Path) -> str:
    return git_out("log", "-1", "--first-parent", "--min-parents=2", "--format=%H", cwd=repo)


def _below_merge(repo: Path) -> str:
    return git_out("rev-parse", f"{_merge(repo)}^1", cwd=repo)


def test_ger_rebase_refuses_to_flatten_a_merge_and_changes_nothing(
    merged: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    head = git_out("rev-parse", "HEAD", cwd=merged)
    code, _out, err = run_cli(merged, rebase_main, [], monkeypatch)
    assert code == ExitCode.USAGE, err
    assert "is a merge commit, which git rebase would leave out" in err
    assert "nothing was changed" in err and "git rebase -i --rebase-merges" in err and "rerere" in err
    assert git_out("rev-parse", "HEAD", cwd=merged) == head


@pytest.mark.parametrize(
    ("main_fn", "argv", "what"),
    [
        (edit_main, ["--drop"], "edit --drop"),
        (reword_main, [], "reword"),
        (edit_main, [], "edit"),
    ],
)
def test_rewriting_a_commit_below_the_merge_refuses(
    merged: Path, monkeypatch: pytest.MonkeyPatch, main_fn, argv: list[str], what: str
) -> None:
    head, merge = git_out("rev-parse", "HEAD", cwd=merged), _merge(merged)
    code, _out, err = run_cli(merged, main_fn, [*argv, _below_merge(merged)], monkeypatch)
    assert code == ExitCode.USAGE, err
    assert "is below the merge commit" in err and f"({merge[:7]}" in err
    assert "ger does not replay merges" in err and f"`ger {what}`" in err
    assert git_out("rev-parse", "HEAD", cwd=merged) == head
    assert git("rev-parse", "-q", "--verify", "REBASE_HEAD", cwd=merged, check=False).returncode != 0


def test_rewording_the_merge_itself_says_it_is_a_merge(merged: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    head = git_out("rev-parse", "HEAD", cwd=merged)
    code, _out, err = run_cli(merged, reword_main, [_merge(merged)], monkeypatch)
    assert code == ExitCode.USAGE, err
    assert "is a merge commit, and ger does not replay merges" in err
    assert "could not find commit" not in err
    assert git_out("rev-parse", "HEAD", cwd=merged) == head


def test_the_commit_above_the_merge_is_rewritten_and_the_merge_kept(
    merged: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    merge = _merge(merged)
    monkeypatch.setenv("GIT_EDITOR", "sed -i 1s/^/reworded:\\ /")
    code, _out, err = run_cli(merged, reword_main, ["HEAD"], monkeypatch)
    assert code == 0, err
    assert git_out("log", "-1", "--format=%s", cwd=merged) == "reworded: C"
    assert git_out("rev-parse", "HEAD^", cwd=merged) == merge
    assert len(git_out("log", "-1", "--format=%P", merge, cwd=merged).split()) == 2


def test_dropping_the_commit_above_the_merge_keeps_the_merge(merged: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    merge = _merge(merged)
    code, _out, err = run_cli(merged, edit_main, ["--drop", "HEAD"], monkeypatch)
    assert code == 0, err
    assert git_out("rev-parse", "HEAD", cwd=merged) == merge


def test_ger_rebase_from_the_merge_rebases_what_is_above_it(merged: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_EDITOR", "true")
    code, _out, err = run_cli(merged, rebase_main, [_merge(merged)], monkeypatch)
    assert code == 0, err


def test_ger_fix_refuses_a_target_below_the_merge(merged: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (merged / "C.txt").write_text("changed\n", encoding="utf-8")
    head = git_out("rev-parse", "HEAD", cwd=merged)
    below = _below_merge(merged)
    code, _out, err = run_cli(merged, fix_main, ["-a", below], monkeypatch)
    assert code == ExitCode.USAGE, err
    assert "could never fold a fixup into it" in err
    assert f"git commit --fixup={below[:7]}" in err and "--rebase-merges --autosquash" in err
    assert git_out("rev-parse", "HEAD", cwd=merged) == head


def test_ger_fix_of_the_commit_above_the_merge_commits_the_fixup(merged: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (merged / "C.txt").write_text("changed\n", encoding="utf-8")
    code, _out, err = run_cli(merged, fix_main, ["-a", "HEAD"], monkeypatch)
    assert code == 0, err
    assert git_out("log", "-1", "--format=%s", cwd=merged) == "fixup! C"


def test_push_preview_warns_about_side_commits_git_push_also_uploads(
    side_merged_in: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, out, err = run_cli(side_merged_in, push_main, ["--dry-run", "--all", "--no-rebase-check"], monkeypatch)
    assert code == 0, (out, err)
    assert "warning: a merge brings in 2 commits this preview leaves out" in err
    assert '"side commit 1"' in err and '"side commit 2"' in err and "--follow-merges" in err
    assert "side commit 1" not in out


def test_push_preview_says_nothing_of_commits_gerrit_already_has(merged: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``U1`` is on ``origin/main``; the side branch, once on ``origin/side``, is too: no new change for either."""
    if git("rev-parse", "-q", "--verify", "side", cwd=merged, check=False).returncode == 0:
        git("update-ref", "refs/remotes/origin/side", "side", cwd=merged)
    code, out, err = run_cli(merged, push_main, ["--dry-run", "--all", "--no-rebase-check"], monkeypatch)
    assert code == 0, (out, err)
    assert "a merge brings in" not in err


def test_follow_merges_lists_the_side_commits_instead_of_warning(
    side_merged_in: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = ["--dry-run", "--all", "--no-rebase-check", "--follow-merges"]
    code, out, err = run_cli(side_merged_in, push_main, argv, monkeypatch)
    assert code == 0, (out, err)
    assert "side commit 1" in out and "a merge brings in" not in err
