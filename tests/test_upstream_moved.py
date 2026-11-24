"""How far the upstream moved on past the local stack, and the rebase onto it previewed: real repositories."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from gerrit_workflow_tools.cli_log import main as log_main
from gerrit_workflow_tools.cli_rebase import main as rebase_main
from gerrit_workflow_tools.core import reorder_preview
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.git_run import git, git_out
from gerrit_workflow_tools.core.reorder_preview import PickStop
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


def _main_moves_on(
    repo: Path, files: dict[str, str], subject: str = "Somebody else's commit", *, blobs: dict[str, str] | None = None
) -> str:
    """A commit on ``main`` with *files* (contents) and *blobs* (blob ids), fetched as ``origin/main``;
    the worktree is not touched."""
    index = repo / ".git" / "upstream-index"
    env = {**_ENV, "GIT_INDEX_FILE": str(index)}
    git("read-tree", "origin/main", cwd=repo, env=env)
    for name, content in files.items():
        blob = git("hash-object", "-w", "--stdin", cwd=repo, input=content).stdout.strip()
        git("update-index", "--add", "--cacheinfo", f"100644,{blob},{name}", cwd=repo, env=env)
    for name, blob in (blobs or {}).items():
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
    assert _moved(stack_repo) == UpstreamMoved(
        "origin/main", 1, preview=False, reason="a merge in the stack: git rebase does not replay it"
    )


def _store(repo: Path) -> ChangeStore:
    return ChangeStore(build_details_by_change_id(stack_rows_mb_to_head(repo)))


def test_ger_log_says_it_in_text_and_json(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(web_stack_repo)
    _code, out, _err = run_cli(web_stack_repo, log_main, ["--json"], monkeypatch, gerrit=store)
    assert json.loads(out)["upstream"] == {
        "name": "origin/main",
        "behind": 0,
        "argv": ["rebase", "origin/main"],
        "conflict": None,
        "preview": True,
        "reason": None,
        "dropped": [],
    }
    _code, out, _err = run_cli(web_stack_repo, log_main, ["--color=never"], monkeypatch, gerrit=store)
    assert "new commit" not in out

    _main_moves_on(web_stack_repo, {"c.txt": "c, from main\n"})
    _code, out, _err = run_cli(web_stack_repo, log_main, ["--json"], monkeypatch, gerrit=store)
    stopping = git_out("rev-parse", "HEAD~1", cwd=web_stack_repo)
    assert json.loads(out)["upstream"] == {
        "name": "origin/main",
        "behind": 1,
        "argv": ["rebase", "origin/main"],
        "conflict": {"sha": stopping, "summary": "test! temporary experiment", "files": ["c.txt"], "kind": "conflict"},
        "preview": True,
        "reason": None,
        "dropped": [],
    }
    _code, out, _err = run_cli(web_stack_repo, log_main, ["--color=never"], monkeypatch, gerrit=store)
    assert (
        "next: origin/main has 1 new commit; rebase onto it with `ger rebase origin/main`; "
        '"test! temporary experiment" would conflict in c.txt'
    ) in out
    _code, out, _err = run_cli(web_stack_repo, log_main, ["--json", "origin/main..feature"], monkeypatch, gerrit=store)
    assert json.loads(out)["upstream"] is None, "an explicit range is no stack on the upstream"


def test_ger_rebase_onto_the_upstream_is_the_rebase_previewed(
    web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ger rebase origin/main`` (what the advice and the extension's button run) replays the stack onto it."""
    tip = _main_moves_on(web_stack_repo, {"z.txt": "z\n"})
    monkeypatch.setenv("GREBASE_EDITOR", "true")
    code, _out, err = run_cli(web_stack_repo, rebase_main, ["origin/main"], monkeypatch, gerrit=_store(web_stack_repo))
    assert code == 0, err
    assert git_out("merge-base", "HEAD", "origin/main", cwd=web_stack_repo) == tip
    assert _moved(web_stack_repo) == UpstreamMoved("origin/main", 0)


def _rebase_i(repo: Path, *, todo: str = "true") -> int:
    """``git rebase -i origin/main``, the todo as it is (or edited by *todo*): what ``ger rebase origin/main`` runs."""
    return git(
        "rebase", "-i", "origin/main", cwd=repo, env={**_ENV, "GIT_SEQUENCE_EDITOR": todo}, check=False
    ).returncode


def test_a_commit_whose_change_is_upstream_under_another_patch_stops_the_rebase_as_empty(stack_repo: Path) -> None:
    """A clean merge that changes nothing: ``git rebase -i`` stops there (``--empty=stop``), and the preview says so."""
    # The stack's own blob: the fixture writes a.txt in text mode, CR LF on Windows.
    same_a = git_out("rev-parse", "HEAD:a.txt", cwd=stack_repo)
    _main_moves_on(stack_repo, {"z.txt": "z\n"}, "Main has a.txt and more", blobs={"a.txt": same_a})
    moved = _moved(stack_repo)
    assert moved is not None and moved.preview and moved.conflict is not None
    assert (moved.conflict.commit.subject, moved.conflict.kind) == ("Refactor parser init", PickStop.EMPTY)
    assert moved.dropped == ()
    assert moved.advice() == (
        "origin/main has 1 new commit; rebase onto it with `ger rebase origin/main`; "
        '"Refactor parser init" would become empty: its change is already upstream'
    )
    assert _rebase_i(stack_repo) != 0, "the rebase it previews stops there"
    assert git_out("diff", "--name-only", "--diff-filter=U", cwd=stack_repo) == "", "on no conflict"
    assert "Refactor parser init" in (stack_repo / ".git" / "rebase-merge" / "message").read_text()


def test_a_commit_already_upstream_is_left_out_as_git_rebase_leaves_it_out(stack_repo: Path) -> None:
    """The same patch upstream, then changed there: git skips the commit, so it cannot conflict."""
    _main_moves_on(stack_repo, {"a.txt": "file a.txt\n"}, "Refactor parser init, picked onto main")
    _main_moves_on(stack_repo, {"a.txt": "changed on main\n"})
    moved = _moved(stack_repo)
    assert moved is not None and moved.preview
    assert moved.conflict is None
    assert [c.subject for c in moved.dropped] == ["Refactor parser init"]
    assert moved.advice().endswith("; 1 commit is already upstream and will be dropped")
    assert _rebase_i(stack_repo) == 0, "the rebase it previews goes through"


def _git_before_2_45(monkeypatch: pytest.MonkeyPatch) -> None:
    """``merge-tree --write-tree`` as git 2.40-2.44 answers a bare tree as ours: exit 1, nothing on stdout."""
    real = reorder_preview.git

    def old_git(*args: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if args[0] == "merge-tree" and "--write-tree" in args:
            ours = args[-2]
            if git_out("cat-file", "-t", ours, cwd=kwargs["cwd"]) != "commit":
                said = f"error: {ours}: expected commit type, but the object dereferences to tree type\n"
                return subprocess.CompletedProcess(["git", *args], 1, "", said)
        return real(*args, **kwargs)

    monkeypatch.setattr(reorder_preview, "git", old_git)


def test_an_old_git_that_takes_no_tree_as_ours_is_handed_commits(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """git 2.40-2.44: ``merge-tree --write-tree`` wants commits on both sides; the preview writes scratch commits."""
    _git_before_2_45(monkeypatch)
    _main_moves_on(stack_repo, {"d.txt": "d, from main\n"})
    moved = _moved(stack_repo)
    assert moved is not None and moved.preview, moved
    assert moved.conflict is not None
    assert (moved.conflict.commit.subject, moved.conflict.files) == ("Cleanup after experiment", ("d.txt",))


def test_an_old_git_refusing_a_tree_is_not_read_as_a_conflict(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit 1 with no tree is not a conflict: a clean rebase of several picks previews clean, as it runs."""
    _git_before_2_45(monkeypatch)
    _main_moves_on(stack_repo, {"z.txt": "z\n"})
    moved = _moved(stack_repo)
    assert moved == UpstreamMoved("origin/main", 1, conflict=None, preview=True)
    assert len(get_stack_snapshot(stack_repo, settings=Settings.from_cwd(stack_repo)).commits) > 2
    assert _rebase_i(stack_repo) == 0, "the rebase it previews goes through"


def test_an_attribute_changed_between_two_calls_shows_in_the_second(stack_repo: Path) -> None:
    """Same commits, same upstream: ``a.txt merge=union`` in ``.git/info/attributes`` merges the conflict away."""
    _main_moves_on(stack_repo, {"a.txt": "file a.txt, from main\n"})
    settings = Settings.from_cwd(stack_repo)
    commits = get_stack_snapshot(stack_repo, settings=settings).commits
    first = upstream_moved(stack_repo, "feature", commits, settings=settings)
    assert first is not None and first.conflict is not None
    assert (first.conflict.commit.subject, first.conflict.files) == ("Refactor parser init", ("a.txt",))
    (stack_repo / ".git" / "info").mkdir(exist_ok=True)
    (stack_repo / ".git" / "info" / "attributes").write_text("a.txt merge=union\n", encoding="utf-8")
    second = upstream_moved(stack_repo, "feature", commits, settings=settings)
    assert second is not None and second.preview and second.conflict is None, second
    assert _rebase_i(stack_repo) == 0, "the rebase it previews goes through"


def test_a_tag_named_like_the_branch_does_not_hide_how_far_the_upstream_moved(stack_repo: Path) -> None:
    tip = _main_moves_on(stack_repo, {"z.txt": "z\n"})
    settings = Settings.from_cwd(stack_repo)
    commits = get_stack_snapshot(stack_repo, settings=settings).commits
    git("tag", "feature", tip, cwd=stack_repo)  # ``feature`` alone now names the tag, already at origin/main
    moved = upstream_moved(stack_repo, "feature", commits, settings=settings)
    assert moved is not None and moved.behind == 1


def test_a_commit_merged_on_gerrit_is_dropped_by_the_one_rebase_ger_log_names(
    web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Merged on Gerrit and upstream moved on: one command, which drops it, and a preview without it."""
    rows = stack_rows_mb_to_head(web_stack_repo)
    store = ChangeStore(build_details_by_change_id(rows, per_index_overrides=[{"status": "MERGED"}]))
    # Gerrit merged it rebased: the same code, another patch than git's patch id would match.
    _main_moves_on(web_stack_repo, {"a.txt": "file a.txt\n", "z.txt": "z\n"}, "Refactor parser init")
    _code, out, _err = run_cli(web_stack_repo, log_main, ["--json"], monkeypatch, gerrit=store)
    upstream = json.loads(out)["upstream"]
    assert upstream["argv"] == ["rebase", "origin/main", "--drop-merged-equivalent"]
    assert upstream["conflict"] is None, "without the merged commit nothing stops the rebase"
    assert upstream["dropped"] == [{"sha": rows[0].sha, "summary": "Refactor parser init"}]
    _code, out, _err = run_cli(web_stack_repo, log_main, ["--color=never"], monkeypatch, gerrit=store)
    commands = [line for line in out.splitlines() if "`ger rebase" in line]
    assert commands == [
        "next: origin/main has 1 new commit; rebase onto it with "
        "`ger rebase origin/main --drop-merged-equivalent`; 1 commit is already upstream and will be dropped"
    ], out
    # What that command runs: the todo with the merged commit dropped (as the enricher drops it), onto origin/main.
    assert _rebase_i(web_stack_repo, todo=f"sed -i 's/^pick {rows[0].short_sha}/drop {rows[0].short_sha}/'") == 0
    assert git_out("log", "--format=%s", "origin/main..HEAD", cwd=web_stack_repo).splitlines()[-1] == (
        "Extract command routing"
    )
