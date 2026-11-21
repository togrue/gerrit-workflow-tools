# Spec: docu/spec/commands/log.md, docu/spec/commands/serve.md
"""The local stack while a rebase is underway: what it replayed so far as it is now, the rest as it was."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_log import main as log_main
from gerrit_workflow_tools.core.annotated_stack import AnnotatedStack, load_annotated_stack
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit_change_status import PatchsetStatus
from gerrit_workflow_tools.core.git_run import git, git_out
from gerrit_workflow_tools.core.rebase_state import RebaseStep, RebaseStop
from gerrit_workflow_tools.core.run_context import RunContext
from gerrit_workflow_tools.server.api import GerApi
from tests.change_store import ChangeStore
from tests.cli_gerrit_mocks import build_details_by_change_id, stack_rows_mb_to_head
from tests.conftest import run_cli
from tests.test_serve import _get


DONE, CURRENT, TODO = RebaseStep.DONE, RebaseStep.CURRENT, RebaseStep.TODO


def _store(repo: Path) -> ChangeStore:
    """The stack as it is before the rebase, pushed: each commit is its change's current patch set."""
    git("config", "gerrit.webUrl", "https://g.example", cwd=repo)
    return ChangeStore(build_details_by_change_id(stack_rows_mb_to_head(repo)))


def _load(repo: Path, store: ChangeStore) -> AnnotatedStack:
    return load_annotated_stack(RunContext.open(repo, settings=Settings.from_cwd(repo), gerrit=store), None)


def _move_upstream_over_c_txt(repo: Path) -> str:
    """origin/main gains a commit writing c.txt, which "test! temporary experiment" adds too."""
    git("checkout", "-q", "main", cwd=repo)
    (repo / "c.txt").write_text("upstream\n", encoding="utf-8")
    git("add", "c.txt", cwd=repo)
    git("commit", "-q", "-m", "Upstream writes c.txt", cwd=repo)
    tip = git_out("rev-parse", "HEAD", cwd=repo)
    git("update-ref", "refs/remotes/origin/main", tip, cwd=repo)
    git("checkout", "-q", "feature", cwd=repo)
    return tip


def test_without_a_rebase_the_stack_has_no_rebase(stack_repo: Path) -> None:
    stack = _load(stack_repo, _store(stack_repo))
    assert stack.rebase is None and len(stack.commits) == 4


def test_an_amended_edit_stop_shows_the_commit_as_it_is_now(stack_repo: Path) -> None:
    store = _store(stack_repo)
    before = stack_rows_mb_to_head(stack_repo)
    git("rebase", "-q", "-i", "HEAD~2", cwd=stack_repo, env={"GIT_SEQUENCE_EDITOR": "sed -i 1s/^pick/edit/"})
    (stack_repo / "c.txt").write_text("amended\n", encoding="utf-8")
    git("commit", "-q", "-a", "--amend", "--no-edit", cwd=stack_repo)
    head = git_out("rev-parse", "HEAD", cwd=stack_repo)

    stack = _load(stack_repo, store)

    assert stack.rebase is not None
    assert [c.sha for c in stack.commits] == [before[0].sha, before[1].sha, head, before[3].sha]
    assert stack.rebase.steps == (None, None, CURRENT, TODO), "the two below HEAD~2 are not replayed at all"
    assert stack.rebase.current is not None and stack.rebase.current.sha == head
    progress = stack.rebase.rebase
    assert (progress.stop, progress.branch, progress.unmerged) == (RebaseStop.EDIT, "feature", ())
    assert progress.orig_head == before[3].sha and progress.onto == before[1].sha
    # The overlay finds the amended commit by its Change-Id: changed here since the last push.
    assert [c.patchset_status for c in stack.commits] == [
        PatchsetStatus.ACTIVE,
        PatchsetStatus.ACTIVE,
        PatchsetStatus.NEWER,
        PatchsetStatus.ACTIVE,
    ]
    assert [c.number for c in stack.commits] == [100, 101, 102, 103]


def test_a_conflict_onto_a_moved_upstream_shows_the_replayed_commits_and_the_stopped_one_as_it_was(
    stack_repo: Path,
) -> None:
    store = _store(stack_repo)
    before = stack_rows_mb_to_head(stack_repo)
    onto = _move_upstream_over_c_txt(stack_repo)
    git("rebase", "-q", "origin/main", cwd=stack_repo, check=False)

    stack = _load(stack_repo, store)

    assert stack.rebase is not None
    shas = [c.sha for c in stack.commits]
    replayed = git_out("rev-list", "--reverse", f"{onto}..HEAD", cwd=stack_repo).split()
    assert shas == [*replayed, before[2].sha, before[3].sha]
    assert len(replayed) == 2 and not set(replayed) & {row.sha for row in before}, "rebased: new commits"
    assert stack.rebase.steps == (DONE, DONE, CURRENT, TODO)
    assert [c.subject for c in stack.rows] == [row.subject for row in before]
    progress = stack.rebase.rebase
    assert (progress.stop, progress.onto, progress.unmerged) == (RebaseStop.CONFLICT, onto, ("c.txt",))
    assert stack.rebase.current is not None and stack.rebase.current.sha == before[2].sha
    assert [c.number for c in stack.commits] == [100, 101, 102, 103]

    # Resolved and added: nothing is conflicted any more, it is ready to continue.
    (stack_repo / "c.txt").write_text("resolved\n", encoding="utf-8")
    git("add", "c.txt", cwd=stack_repo)
    resolved = _load(stack_repo, store)
    assert resolved.rebase is not None
    assert (resolved.rebase.rebase.stop, resolved.rebase.rebase.unmerged) == (RebaseStop.RESOLVED, ())
    assert resolved.rebase.steps == (DONE, DONE, CURRENT, TODO)


def test_an_explicit_range_during_a_rebase_is_the_range(stack_repo: Path) -> None:
    store = _store(stack_repo)
    git("rebase", "-q", "-i", "HEAD~2", cwd=stack_repo, env={"GIT_SEQUENCE_EDITOR": "sed -i 1s/^pick/edit/"})
    run = RunContext.open(stack_repo, settings=Settings.from_cwd(stack_repo), gerrit=store)
    stack = load_annotated_stack(run, "feature@{upstream}..feature")
    assert stack.rebase is None and len(stack.commits) == 4


def test_ger_log_and_serve_show_the_same_stack_during_a_rebase(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One model, two shells: the commits, where the rebase stands on each, and why it stopped."""
    store = _store(stack_repo)
    _move_upstream_over_c_txt(stack_repo)
    git("rebase", "-q", "origin/main", cwd=stack_repo, check=False)

    code, out, err = run_cli(stack_repo, log_main, ["--json"], monkeypatch, gerrit=store)
    served = _get(GerApi(stack_repo, gerrit=store), "/stack")

    assert code == 0, err
    logged = json.loads(out)
    assert [(c["sha"], c["rebase"]) for c in logged["commits"]] == [(c["sha"], c["rebase"]) for c in served["commits"]]
    assert [c["rebase"] for c in served["commits"]] == ["done", "done", "current", "todo"]
    assert logged["rebase"] == served["rebase"]
    assert (served["rebase"]["stop"], served["rebase"]["unmerged"]) == ("conflict", ["c.txt"])
    current = served["commits"][2]
    note = (
        f"note: a rebase is in progress on feature, stopped on a conflict at {current['short_sha']} "
        '"test! temporary experiment" in c.txt; 1 commit still to replay, shown as before.'
    )
    assert note in err

    (stack_repo / "c.txt").write_text("resolved\n", encoding="utf-8")
    git("add", "c.txt", cwd=stack_repo)
    _code, _out, err = run_cli(stack_repo, log_main, [], monkeypatch, gerrit=store)
    assert "with its conflicts resolved, ready for `git rebase --continue`" in err
