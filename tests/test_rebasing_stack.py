# Spec: docu/spec/commands/log.md, docu/spec/commands/serve.md
"""The local stack while a rebase is underway: what it replayed so far as it is now, the rest as it was."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_log import main as log_main
from gerrit_workflow_tools.cli_push import main as push_main
from gerrit_workflow_tools.core.annotated_stack import AnnotatedStack, load_annotated_stack
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit_change_status import PatchsetStatus
from gerrit_workflow_tools.core.git_run import git, git_out
from gerrit_workflow_tools.core.git_state import git_dir
from gerrit_workflow_tools.core.rebase_state import RebaseStop, rebase_in_progress, rebase_progress, rebase_refusal
from gerrit_workflow_tools.core.rebasing_stack import RebaseStep
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


def _stop_to_edit(repo: Path, todo_edit: str = "1s/^pick/edit/", onto: str = "HEAD~2") -> None:
    git("rebase", "-q", "-i", onto, cwd=repo, env={"GIT_SEQUENCE_EDITOR": f"sed -i -e {todo_edit}"})


def _commit_file(repo: Path, name: str, subject: str) -> str:
    (repo / name).write_text(f"{subject}\n", encoding="utf-8")
    git("add", name, cwd=repo)
    git("commit", "-q", "-m", subject, cwd=repo)
    return git_out("rev-parse", "HEAD", cwd=repo)


def test_a_conflict_resolved_and_committed_by_hand_shows_the_commit_once_as_it_is_now(stack_repo: Path) -> None:
    """``git commit`` instead of ``--continue``: git keeps REBASE_HEAD, but the commit is applied, at HEAD."""
    store = _store(stack_repo)
    before = stack_rows_mb_to_head(stack_repo)
    onto = _move_upstream_over_c_txt(stack_repo)
    git("rebase", "-q", "origin/main", cwd=stack_repo, check=False)
    (stack_repo / "c.txt").write_text("resolved\n", encoding="utf-8")
    git("add", "c.txt", cwd=stack_repo)
    git("commit", "-q", "--no-edit", cwd=stack_repo)
    replayed = git_out("rev-list", "--reverse", f"{onto}..HEAD", cwd=stack_repo).split()

    stack = _load(stack_repo, store)

    assert stack.rebase is not None
    assert [c.sha for c in stack.commits] == [*replayed, before[3].sha]
    assert stack.rebase.steps == (DONE, DONE, CURRENT, TODO)
    assert [c.subject for c in stack.rows] == [row.subject for row in before]
    progress = stack.rebase.rebase
    assert (progress.stop, progress.applied, progress.unmerged) == (RebaseStop.EDIT, True, ())
    assert stack.rebase.current is not None and stack.rebase.current.sha == replayed[-1]


def test_a_commit_reset_away_at_an_edit_stop_is_not_shown_and_commits_made_there_are_done(stack_repo: Path) -> None:
    """Splitting at an edit stop: ``git reset HEAD~``, then new commits; none of them is the stopped one."""
    store = _store(stack_repo)
    before = stack_rows_mb_to_head(stack_repo)
    _stop_to_edit(stack_repo)
    git("reset", "-q", "HEAD~", cwd=stack_repo)

    reset = _load(stack_repo, store)
    assert reset.rebase is not None
    assert [c.sha for c in reset.commits] == [before[0].sha, before[1].sha, before[3].sha]
    assert reset.rebase.steps == (None, None, TODO)
    assert (reset.rebase.rebase.stop, reset.rebase.current) == (RebaseStop.SPLIT, None)
    assert reset.rebase.rebase.current == before[2].sha

    part = _commit_file(stack_repo, "c.txt", "c part 1")
    split = _load(stack_repo, store)
    assert split.rebase is not None
    assert [c.sha for c in split.commits] == [before[0].sha, before[1].sha, part, before[3].sha]
    assert split.rebase.steps == (None, None, DONE, TODO)
    assert (split.rebase.rebase.stop, split.rebase.current) == (RebaseStop.SPLIT, None)


def test_a_commit_made_on_top_of_an_edit_stop_is_done_and_the_stopped_commit_stays_current(stack_repo: Path) -> None:
    store = _store(stack_repo)
    before = stack_rows_mb_to_head(stack_repo)
    _stop_to_edit(stack_repo)
    added = _commit_file(stack_repo, "e.txt", "Add e.txt")

    stack = _load(stack_repo, store)

    assert stack.rebase is not None
    assert [c.sha for c in stack.commits] == [*(row.sha for row in before[:3]), added, before[3].sha]
    assert stack.rebase.steps == (None, None, CURRENT, DONE, TODO)
    assert stack.rebase.current is not None and stack.rebase.current.sha == before[2].sha
    assert stack.rebase.rebase.stop is RebaseStop.SPLIT


def test_ger_log_and_serve_name_a_split_stop(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(stack_repo)
    before = stack_rows_mb_to_head(stack_repo)
    _stop_to_edit(stack_repo)
    git("reset", "-q", "HEAD~", cwd=stack_repo)
    _commit_file(stack_repo, "c.txt", "c part 1")

    code, out, err = run_cli(stack_repo, log_main, ["--json"], monkeypatch, gerrit=store)
    served = _get(GerApi(stack_repo, gerrit=store), "/stack")

    assert code == 0, err
    assert json.loads(out)["rebase"] == served["rebase"]
    assert (served["rebase"]["stop"], served["rebase"]["current"]) == ("split", None)
    assert [c["rebase"] for c in served["commits"]] == [None, None, "done", "todo"]
    assert (
        f"stopped at {before[2].sha[:7]} to edit it, and HEAD has moved on from it: "
        "the commits above it were made at the stop" in err
    )


def test_an_amend_at_an_edit_stop_that_keeps_the_change_id_is_still_the_stopped_commit(stack_repo: Path) -> None:
    """``git reset --soft HEAD~`` then ``git commit -C``: an amend by hand, same parent, same Change-Id."""
    store = _store(stack_repo)
    before = stack_rows_mb_to_head(stack_repo)
    _stop_to_edit(stack_repo)
    git("reset", "-q", "--soft", "HEAD~", cwd=stack_repo)
    (stack_repo / "c.txt").write_text("amended\n", encoding="utf-8")
    git("add", "c.txt", cwd=stack_repo)
    git("commit", "-q", "-C", before[2].sha, cwd=stack_repo)
    head = git_out("rev-parse", "HEAD", cwd=stack_repo)

    stack = _load(stack_repo, store)

    assert stack.rebase is not None
    assert stack.rebase.steps == (None, None, CURRENT, TODO)
    assert stack.rebase.current is not None and stack.rebase.current.sha == head
    assert stack.rebase.rebase.stop is RebaseStop.EDIT


def test_a_conflict_under_the_apply_backend_reads_as_a_conflict(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``git rebase --apply`` keeps no todo, but it does stop on a conflict, at a commit it names."""
    store = _store(stack_repo)
    before = stack_rows_mb_to_head(stack_repo)
    _move_upstream_over_c_txt(stack_repo)
    git("rebase", "-q", "--apply", "origin/main", cwd=stack_repo, check=False)
    api = GerApi(stack_repo, gerrit=store)

    served = _get(api, "/stack")
    assert served["rebase"]["stop"] == "conflict"
    assert (served["rebase"]["applied"], served["rebase"]["unmerged"]) == (False, ["c.txt"])
    assert served["rebase"]["as_before"] is True
    assert [c["sha"] for c in served["commits"]] == [row.sha for row in before]
    _code, _out, err = run_cli(stack_repo, log_main, [], monkeypatch, gerrit=store)
    assert "stopped on a conflict in c.txt; this shows the branch as it was before the rebase" in err

    (stack_repo / "c.txt").write_text("resolved\n", encoding="utf-8")
    git("add", "c.txt", cwd=stack_repo)
    assert _get(api, "/stack")["rebase"]["stop"] == "resolved"

    # Committed by hand: HEAD moved off where git stopped; what --continue would do cannot be told.
    git("commit", "-q", "-m", "resolved by hand", cwd=stack_repo)
    assert _get(api, "/stack")["rebase"]["stop"] is None


def test_a_live_rebase_is_not_served_as_before(stack_repo: Path) -> None:
    store = _store(stack_repo)
    _stop_to_edit(stack_repo)
    assert _get(GerApi(stack_repo, gerrit=store), "/stack")["rebase"]["as_before"] is False


def test_a_rebase_of_a_detached_head_shows_the_commits_above_onto(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No branch, so no upstream: the stack is what the rebase replays, above its ``onto``."""
    store = _store(stack_repo)
    before = stack_rows_mb_to_head(stack_repo)
    git("checkout", "-q", "--detach", cwd=stack_repo)
    _stop_to_edit(stack_repo)

    code, out, err = run_cli(stack_repo, log_main, ["--json"], monkeypatch, gerrit=store)
    served = _get(GerApi(stack_repo, gerrit=store), "/stack")

    assert code == 0, err
    logged = json.loads(out)
    assert [(c["sha"], c["rebase"]) for c in logged["commits"]] == [
        (before[2].sha, "current"),
        (before[3].sha, "todo"),
    ]
    assert [(c["sha"], c["rebase"]) for c in served["commits"]] == [(c["sha"], c["rebase"]) for c in logged["commits"]]
    assert logged["rebase"] == served["rebase"]
    assert (served["rebase"]["branch"], served["rebase"]["stop"], served["branch"]) == (None, "edit", None)
    assert "a rebase is in progress on a detached HEAD, stopped at" in err
    assert (logged["stack"]["target_branch"], served["stack"]["push_branch"]) == (None, None)
    assert {c["patchset_status"] for c in served["commits"]} == {"unknown"}, "no push branch to find them on"
    code, out, err = run_cli(stack_repo, log_main, [], monkeypatch, gerrit=store)
    assert code == 0, err
    assert "temporary experiment" in out


def test_a_fixup_still_to_replay_is_its_own_row_and_counted(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(stack_repo)
    before = stack_rows_mb_to_head(stack_repo)
    _stop_to_edit(stack_repo, "1s/^pick/edit/ -e 3s/^pick/fixup/", onto="HEAD~3")

    stack = _load(stack_repo, store)

    assert stack.rebase is not None
    assert [c.sha for c in stack.commits] == [row.sha for row in before]
    assert stack.rebase.steps == (None, CURRENT, TODO, TODO)
    _code, _out, err = run_cli(stack_repo, log_main, [], monkeypatch, gerrit=store)
    assert "; 2 commits still to replay, shown as before." in err


def test_a_fixup_stopped_on_a_conflict_shows_the_fixup_as_the_stopped_commit(stack_repo: Path) -> None:
    """git writes ``amend`` (the commit it folds into) for a conflicting fixup: that is no edit stop."""
    store = _store(stack_repo)
    before = stack_rows_mb_to_head(stack_repo)
    _commit_file(stack_repo, "x.txt", "Write x.txt")
    fixup = _commit_file(stack_repo, "x.txt", "Rewrite x.txt")
    # pick the top stack commit, drop "Write x.txt": the fixup's change to x.txt has nothing to apply to.
    todo = "-e 2s/^pick/drop/ -e 3s/^pick/fixup/"
    git("rebase", "-q", "-i", "HEAD~3", cwd=stack_repo, env={"GIT_SEQUENCE_EDITOR": f"sed -i {todo}"}, check=False)
    state = git_dir(stack_repo)
    assert state is not None and (state / "rebase-merge" / "amend").exists(), "the case git leaves behind"

    stack = _load(stack_repo, store)

    assert stack.rebase is not None
    assert [c.sha for c in stack.commits] == [*(row.sha for row in before), fixup]
    assert stack.rebase.steps == (None, None, None, DONE, CURRENT)
    assert stack.rebase.current is not None and stack.rebase.current.sha == fixup
    progress = stack.rebase.rebase
    assert (progress.stop, progress.applied, progress.unmerged) == (RebaseStop.CONFLICT, False, ("x.txt",))


def _am_stopped(repo: Path) -> None:
    """``git am`` of a patch that does not apply: it stops, with ``rebase-apply`` on disk."""
    patches = repo.parent / "patches"
    git("format-patch", "-q", "-1", "-o", str(patches), "HEAD", cwd=repo)
    git("reset", "-q", "--hard", "HEAD~", cwd=repo)
    _commit_file(repo, "d.txt", "Write d.txt first")
    git("am", "-q", *(str(p) for p in sorted(patches.iterdir())), cwd=repo, check=False)


def test_a_git_am_underway_is_not_a_rebase(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(stack_repo)
    _am_stopped(stack_repo)
    assert (git_dir(stack_repo) / "rebase-apply" / "applying").exists()  # type: ignore[operator]

    assert rebase_progress(stack_repo) is None and not rebase_in_progress(stack_repo)
    served = _get(GerApi(stack_repo, gerrit=store), "/stack")
    assert (served["rebase"], served["rebase_in_progress"], served["branch"]) == (None, False, "feature")
    code, _out, err = run_cli(stack_repo, log_main, [], monkeypatch, gerrit=store)
    assert code == 0, err
    assert "rebase" not in err


def test_a_git_am_underway_refuses_push_and_rewrites_with_its_own_reason(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``feature`` moves with each patch applied: a push now would send it half applied."""
    _am_stopped(stack_repo)
    am = "a `git am` is in progress; finish it (`git am --continue`) or `git am --abort`"
    assert rebase_refusal(stack_repo) == f"{am} first"
    code, _out, err = run_cli(stack_repo, push_main, ["--dry-run"], monkeypatch, gerrit=ChangeStore({}))
    assert code == ExitCode.USAGE
    assert f"{am}, then push" in err


def test_an_amend_at_an_edit_stop_onto_a_moved_upstream_is_the_stopped_commit(stack_repo: Path) -> None:
    """The commits below were replayed with new ids (``rewritten-list``): the amend is still told from a split."""
    store = _store(stack_repo)
    git("checkout", "-q", "main", cwd=stack_repo)
    git(
        "update-ref",
        "refs/remotes/origin/main",
        _commit_file(stack_repo, "u.txt", "Upstream adds u.txt"),
        cwd=stack_repo,
    )
    git("checkout", "-q", "feature", cwd=stack_repo)
    _stop_to_edit(stack_repo, "3s/^pick/edit/", onto="origin/main")
    (stack_repo / "c.txt").write_text("amended\n", encoding="utf-8")
    git("commit", "-q", "-a", "--amend", "--no-edit", cwd=stack_repo)

    stack = _load(stack_repo, store)

    assert stack.rebase is not None
    assert stack.rebase.steps == (DONE, DONE, CURRENT, TODO)
    assert stack.rebase.rebase.stop is RebaseStop.EDIT
    assert stack.rebase.current is not None
    assert stack.rebase.current.sha == git_out("rev-parse", "HEAD", cwd=stack_repo)


def test_a_detached_rebase_does_not_borrow_a_branch_that_points_where_it_stopped(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HEAD moves with the rebase: a branch sitting at the stop is not the one being worked on."""
    store = _store(stack_repo)
    before = stack_rows_mb_to_head(stack_repo)
    git("branch", "part1", before[2].sha, cwd=stack_repo)
    git("branch", "--set-upstream-to", "origin/main", "part1", cwd=stack_repo)
    git("checkout", "-q", "--detach", cwd=stack_repo)
    _stop_to_edit(stack_repo)  # stops at before[2], where part1 points

    code, out, err = run_cli(stack_repo, log_main, ["--json"], monkeypatch, gerrit=store)

    assert code == 0, err
    logged = json.loads(out)
    assert [(c["sha"], c["rebase"]) for c in logged["commits"]] == [(before[2].sha, "current"), (before[3].sha, "todo")]
    assert (logged["rebase"]["branch"], logged["stack"]["push_branch"]) == (None, None)


def test_an_amend_with_commits_made_on_top_stays_the_current_commit(stack_repo: Path) -> None:
    store = _store(stack_repo)
    _stop_to_edit(stack_repo)
    (stack_repo / "c.txt").write_text("amended\n", encoding="utf-8")
    git("commit", "-q", "-a", "--amend", "--no-edit", cwd=stack_repo)
    amended = git_out("rev-parse", "HEAD", cwd=stack_repo)
    on_top = _commit_file(stack_repo, "f.txt", "Made at the stop")

    stack = _load(stack_repo, store)

    assert stack.rebase is not None and stack.rebase.current is not None
    assert stack.rebase.current.sha == amended, "the amend is still the stopped change"
    shas = [c.sha for c in stack.commits]
    assert stack.rebase.steps[shas.index(amended)] is CURRENT and stack.rebase.steps[shas.index(on_top)] is DONE
    assert stack.rebase.rebase.moved


def test_an_explicit_range_during_a_live_rebase_is_not_served_as_before(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(stack_repo)
    _stop_to_edit(stack_repo)
    code, out, err = run_cli(stack_repo, log_main, ["--json", "origin/main..HEAD"], monkeypatch, gerrit=store)
    assert code == 0, err
    assert json.loads(out)["rebase"]["as_before"] is False
    assert "as it was before the rebase" not in err and "the range asked for, as it is now" in err
