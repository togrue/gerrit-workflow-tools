"""How far the upstream moved on past the local stack, and the rebase onto it previewed: real repositories."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_log import main as log_main
from gerrit_workflow_tools.cli_rebase import main as rebase_main
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.git_run import git, git_out
from gerrit_workflow_tools.core.stack import get_stack_snapshot
from gerrit_workflow_tools.core.upstream_moved import UpstreamMoved, upstream_moved
from tests.change_store import ChangeStore
from tests.cli_gerrit_mocks import build_details_by_change_id, stack_rows_mb_to_head
from tests.conftest import run_cli


_ENV = {
    "GIT_AUTHOR_NAME": "alice",
    "GIT_AUTHOR_EMAIL": "alice@example.com",
    "GIT_COMMITTER_NAME": "alice",
    "GIT_COMMITTER_EMAIL": "alice@example.com",
}


def _main_moves_on(repo: Path, files: dict[str, str], subject: str = "Somebody else's commit") -> str:
    """A commit on ``main`` with *files*, fetched as ``origin/main``; the worktree is not touched."""
    index = repo / ".git" / "upstream-index"
    env = {**_ENV, "GIT_INDEX_FILE": str(index)}
    git("read-tree", "origin/main", cwd=repo, env=env)
    for name, content in files.items():
        blob = git("hash-object", "-w", "--stdin", cwd=repo, input=content).stdout.strip()
        git("update-index", "--add", "--cacheinfo", f"100644,{blob},{name}", cwd=repo, env=env)
    tree = git_out("write-tree", cwd=repo, env=env)
    index.unlink()
    commit = git_out("commit-tree", tree, "-p", "origin/main", "-m", subject, cwd=repo, env=_ENV)
    git("update-ref", "refs/remotes/origin/main", commit, cwd=repo)
    return commit


def _moved(repo: Path) -> UpstreamMoved | None:
    settings = Settings.from_cwd(repo)
    return upstream_moved(repo, "feature", get_stack_snapshot(repo, settings=settings).commits, settings=settings)


def _untouched(repo: Path) -> tuple[str, str]:
    return git_out("rev-parse", "HEAD", cwd=repo), git_out("status", "--porcelain", cwd=repo)


def test_an_upstream_that_has_not_moved_is_zero_behind(stack_repo: Path) -> None:
    assert _moved(stack_repo) == UpstreamMoved("origin/main", 0)


def test_no_upstream_says_nothing(stack_repo: Path) -> None:
    git("branch", "--unset-upstream", "feature", cwd=stack_repo)
    settings = Settings.from_cwd(stack_repo)
    assert upstream_moved(stack_repo, "feature", [], settings=settings) is None
    assert upstream_moved(stack_repo, None, [], settings=settings) is None


def test_an_upstream_moved_on_without_conflict_rebases_cleanly(stack_repo: Path) -> None:
    before = _untouched(stack_repo)
    _main_moves_on(stack_repo, {"z.txt": "z\n"})
    _main_moves_on(stack_repo, {"y.txt": "y\n"})
    assert _moved(stack_repo) == UpstreamMoved("origin/main", 2, conflict=None, preview=True)
    assert _untouched(stack_repo) == before, "previewed without touching HEAD, the index or the worktree"


def test_a_conflict_on_a_middle_commit_names_that_commit_and_its_files(stack_repo: Path) -> None:
    before = _untouched(stack_repo)
    _main_moves_on(stack_repo, {"b.txt": "b, from main\n"})
    moved = _moved(stack_repo)
    assert moved is not None and moved.behind == 1 and moved.preview
    assert moved.conflict is not None
    assert (moved.conflict.commit.subject, moved.conflict.files) == ("Extract command routing", ("b.txt",))
    assert _untouched(stack_repo) == before
    assert moved.advice() == (
        "origin/main has 1 new commit; rebase onto it with `ger rebase origin/main`; "
        '"Extract command routing" would conflict in b.txt'
    )
    # The rebase it previews stops there.
    assert git("rebase", "origin/main", cwd=stack_repo, env=_ENV, check=False).returncode != 0
    assert git_out("diff", "--name-only", "--diff-filter=U", cwd=stack_repo) == "b.txt"
    assert "Extract command routing" in (stack_repo / ".git" / "rebase-merge" / "message").read_text()


def test_skewed_commit_dates_count_as_git_status_does(stack_repo: Path) -> None:
    """A rebase with an old committer date: ``branch..upstream`` alone would count the base as new."""
    for i in range(20):  # long enough for git's walk of ``branch..upstream`` to give up early
        (stack_repo / "n.txt").write_text(f"{i}\n", encoding="utf-8")
        git("add", "n.txt", cwd=stack_repo)
        git("commit", "-q", "-m", f"Commit {i}", cwd=stack_repo, env=_ENV)
    git(
        "rebase",
        "--force-rebase",
        "origin/main",
        cwd=stack_repo,
        env={**_ENV, "GIT_COMMITTER_DATE": "2001-01-01T00:00:00"},
    )
    assert _moved(stack_repo) == UpstreamMoved("origin/main", 0)


def test_a_merge_in_the_stack_is_not_previewed(stack_repo: Path) -> None:
    git("checkout", "-q", "-b", "side", "HEAD~1", cwd=stack_repo)
    (stack_repo / "s.txt").write_text("side\n", encoding="utf-8")
    git("add", "s.txt", cwd=stack_repo)
    git("commit", "-q", "-m", "Side", cwd=stack_repo, env=_ENV)
    git("checkout", "-q", "feature", cwd=stack_repo)
    git("merge", "-q", "--no-edit", "side", cwd=stack_repo, env=_ENV)
    _main_moves_on(stack_repo, {"z.txt": "z\n"})
    assert _moved(stack_repo) == UpstreamMoved("origin/main", 1, preview=False)


def _store(repo: Path) -> ChangeStore:
    return ChangeStore(build_details_by_change_id(stack_rows_mb_to_head(repo)))


def test_ger_log_says_it_in_text_and_json(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "gerrit.webUrl", "https://g.example", cwd=stack_repo)
    store = _store(stack_repo)
    _code, out, _err = run_cli(stack_repo, log_main, ["--json"], monkeypatch, gerrit=store)
    assert json.loads(out)["upstream"] == {"name": "origin/main", "behind": 0, "conflict": None, "preview": True}
    _code, out, _err = run_cli(stack_repo, log_main, ["--color=never"], monkeypatch, gerrit=store)
    assert "new commit" not in out

    _main_moves_on(stack_repo, {"c.txt": "c, from main\n"})
    _code, out, _err = run_cli(stack_repo, log_main, ["--json"], monkeypatch, gerrit=store)
    stopping = git_out("rev-parse", "HEAD~1", cwd=stack_repo)
    assert json.loads(out)["upstream"] == {
        "name": "origin/main",
        "behind": 1,
        "conflict": {"sha": stopping, "summary": "test! temporary experiment", "files": ["c.txt"]},
        "preview": True,
    }
    _code, out, _err = run_cli(stack_repo, log_main, ["--color=never"], monkeypatch, gerrit=store)
    assert (
        "next: origin/main has 1 new commit; rebase onto it with `ger rebase origin/main`; "
        '"test! temporary experiment" would conflict in c.txt'
    ) in out
    _code, out, _err = run_cli(stack_repo, log_main, ["--json", "origin/main..feature"], monkeypatch, gerrit=store)
    assert json.loads(out)["upstream"] is None, "an explicit range is no stack on the upstream"


def test_ger_rebase_onto_the_upstream_is_the_rebase_previewed(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ger rebase origin/main`` (what the advice and the extension's button run) replays the stack onto it."""
    git("config", "gerrit.webUrl", "https://g.example", cwd=stack_repo)
    tip = _main_moves_on(stack_repo, {"z.txt": "z\n"})
    monkeypatch.setenv("GREBASE_EDITOR", "true")
    code, _out, err = run_cli(stack_repo, rebase_main, ["origin/main"], monkeypatch, gerrit=_store(stack_repo))
    assert code == 0, err
    assert git_out("merge-base", "HEAD", "origin/main", cwd=stack_repo) == tip
    assert _moved(stack_repo) == UpstreamMoved("origin/main", 0)
