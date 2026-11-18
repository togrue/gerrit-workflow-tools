"""One reason, one message, one code — whichever command meets it.

Pins agreement *between* commands for the refusals the shared runtime owns: outside a git
repository, a branch without an upstream, a work tree a history rewrite would trample, and a
prompt with no terminal to answer it.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_changeid import main as change_id_main
from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_edit import main as edit_main, main_reword
from gerrit_workflow_tools.cli_fix import main as fix_main
from gerrit_workflow_tools.cli_ger import main as ger_main
from gerrit_workflow_tools.cli_log import main as log_main
from gerrit_workflow_tools.cli_push import main as push_main
from gerrit_workflow_tools.cli_rebase import main as rebase_main
from gerrit_workflow_tools.cli_show import main as show_main
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.git_run import git
from gerrit_workflow_tools.core.rebase_state import history_rewrite_refusal
from gerrit_workflow_tools.core.reorder import ReorderRefusal, StackStep, UncommittedChanges, rewrite_stack
from gerrit_workflow_tools.core.stack import get_stack_snapshot
from tests.change_store import ChangeStore
from tests.conftest import run_cli


def _configure(repo: Path) -> None:
    git("config", "gerrit.webUrl", "https://g.example", cwd=repo)


@pytest.mark.parametrize(
    ("main_fn", "argv"),
    [
        (log_main, []),
        (show_main, []),
        (edit_main, []),
        (main_reword, ["HEAD"]),
        (fix_main, ["HEAD"]),
        (push_main, ["--dry-run"]),
        (rebase_main, []),
    ],
)
def test_every_command_says_not_a_git_repository_the_same_way(
    main_fn: Callable[..., int], argv: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "plain"
    outside.mkdir()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    code, out, err = run_cli(outside, main_fn, argv, monkeypatch)
    assert (code, out, err.strip()) == (ExitCode.GIT, "", "error: not a git repository; run ger inside a clone")


@pytest.mark.parametrize(("main_fn", "argv"), [(log_main, []), (push_main, ["--dry-run"]), (push_main, ["-y"])])
def test_a_missing_upstream_reads_the_same_in_log_and_push(
    main_fn: Callable[..., int], argv: list[str], stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(stack_repo)
    git("branch", "--unset-upstream", cwd=stack_repo)
    code, _out, err = run_cli(stack_repo, main_fn, argv, monkeypatch)
    assert code == ExitCode.CONFIG
    assert err.startswith("error: No upstream configured for branch 'feature'.")
    assert "git branch --set-upstream-to=<remote>/<branch>" in err


@pytest.mark.parametrize(("main_fn", "argv"), [(edit_main, ["HEAD~1"]), (main_reword, ["HEAD~1"]), (rebase_main, [])])
def test_every_history_rewrite_refuses_a_dirty_tree_the_same_way(
    main_fn: Callable[..., int], argv: list[str], stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(stack_repo)
    tracked = git("ls-files", cwd=stack_repo).stdout.split()[0]
    (stack_repo / tracked).write_text("uncommitted\n", encoding="utf-8")
    code, _out, err = run_cli(stack_repo, main_fn, argv, monkeypatch, gerrit=ChangeStore({}))
    assert code == ExitCode.USAGE
    assert err.strip() == (
        "error: you have uncommitted changes; commit them (`ger fix REV` folds them into a stack commit) "
        "or `git stash` them first"
    )


def _add_submodule(repo: Path, tmp_path: Path, name: str = "lib") -> Path:
    """A submodule *name* with two commits, recorded (and checked out) at the second."""
    lib = tmp_path / name
    lib.mkdir()
    git("init", "-b", "main", cwd=lib)
    for text in ("a\n", "b\n"):
        (lib / "a.txt").write_text(text, encoding="utf-8")
        git("add", ".", cwd=lib)
        git("commit", "-m", f"lib {text.strip()}", cwd=lib)
    git("-c", "protocol.file.allow=always", "submodule", "add", str(lib), name, cwd=repo)
    git("commit", "-m", f"add {name}", cwd=repo)
    return repo / name


def _modify_submodule_content(repo: Path, sub: Path) -> None:
    (sub / "a.txt").write_text("dirty\n", encoding="utf-8")


def _add_untracked_to_submodule(repo: Path, sub: Path) -> None:
    (sub / "untracked.txt").write_text("new\n", encoding="utf-8")


def _check_out_another_submodule_commit(repo: Path, sub: Path) -> None:
    git("checkout", "-q", "HEAD~1", cwd=sub)


def _stage_another_submodule_commit(repo: Path, sub: Path) -> None:
    git("checkout", "-q", "HEAD~1", cwd=sub)
    git("add", sub.name, cwd=repo)


def _modify_a_tracked_file(repo: Path, sub: Path) -> None:
    tracked = next(f for f in git("ls-files", cwd=repo).stdout.split() if f not in ("lib", ".gitmodules"))
    (repo / tracked).write_text("uncommitted\n", encoding="utf-8")


_STASH_IT = "you have uncommitted changes"
_STAGED_LIB = (
    "the submodule commit staged for lib is not committed; commit it or `git restore --staged lib` "
    "first (a rebase would stop on it, or fold it into the commit it stops at)"
)


@pytest.mark.parametrize(
    ("make_dirty", "refusal"),
    [
        (_modify_submodule_content, None),
        (_add_untracked_to_submodule, None),
        (_check_out_another_submodule_commit, None),
        (_modify_a_tracked_file, _STASH_IT),
    ],
)
def test_a_history_rewrite_counts_as_uncommitted_exactly_what_git_stash_would_save(
    make_dirty: Callable[[Path, Path], None],
    refusal: str | None,
    stack_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ger edit asks for a stash exactly when ``git stash`` has something to save (#35).

    Dirt inside a submodule, or a submodule checked out at another commit than the one recorded,
    is neither saved by ``git stash`` nor in the way of ``git rebase``: no refusal. A tracked file of
    the superproject is the control.
    """
    _configure(stack_repo)
    sub = _add_submodule(stack_repo, tmp_path)
    make_dirty(stack_repo, sub)
    git_would_stash = bool(git("stash", "create", cwd=stack_repo).stdout.strip())
    assert git_would_stash is (refusal is not None)

    said = history_rewrite_refusal(stack_repo)
    assert (said is not None) is git_would_stash
    code, _out, err = run_cli(stack_repo, edit_main, ["HEAD~1"], monkeypatch, gerrit=ChangeStore({}))
    assert ("uncommitted changes" in err) is git_would_stash, err
    assert code == (ExitCode.USAGE if git_would_stash else ExitCode.OK), err


@pytest.mark.parametrize(("main_fn", "argv"), [(edit_main, ["HEAD~1"]), (main_reword, ["HEAD~1"]), (rebase_main, [])])
def test_a_submodule_staged_at_another_commit_stops_a_history_rewrite_without_asking_for_a_stash(
    main_fn: Callable[..., int], argv: list[str], stack_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``git stash`` saves nothing for it, yet the rebase's checkout refuses to overwrite it."""
    _configure(stack_repo)
    sub = _add_submodule(stack_repo, tmp_path)
    _stage_another_submodule_commit(stack_repo, sub)
    assert not git("stash", "create", cwd=stack_repo).stdout.strip()

    assert history_rewrite_refusal(stack_repo) == _STAGED_LIB
    code, _out, err = run_cli(stack_repo, main_fn, argv, monkeypatch, gerrit=ChangeStore({}))
    assert (code, err.strip()) == (ExitCode.USAGE, f"error: {_STAGED_LIB}")


def test_the_unstage_command_for_two_staged_submodules_runs_as_given(
    stack_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One copy-pasteable command for every staged path, even one git would C-quote."""
    _configure(stack_repo)
    names = ["lib", "lib ü"]
    for sub in [_add_submodule(stack_repo, tmp_path, name) for name in names]:
        _stage_another_submodule_commit(stack_repo, sub)
    staged = git("diff", "--cached", "--name-only", "-z", cwd=stack_repo).stdout.split("\0")
    assert sorted(p for p in staged if p) == names

    said = history_rewrite_refusal(stack_repo)
    assert said is not None
    assert said.startswith("the submodule commit staged for lib, lib ü is not committed;"), said
    command = said.split("`")[1]
    assert command == "git restore --staged lib 'lib ü'"
    subprocess.run(["sh", "-c", command], cwd=stack_repo, check=True)
    assert not git("diff", "--cached", "--name-only", cwd=stack_repo).stdout.strip()
    assert history_rewrite_refusal(stack_repo) is None


@pytest.mark.parametrize(("main_fn", "argv"), [(edit_main, ["HEAD~1"]), (main_reword, ["HEAD~1"]), (rebase_main, [])])
def test_a_submodule_staged_below_the_stack_stops_a_history_rewrite_up_front(
    main_fn: Callable[..., int], argv: list[str], stack_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The submodule is in the base and no stack commit touches it: git's checkout has nothing to
    refuse, yet the rebase would stop at its first commit. ger refuses before it starts."""
    _configure(stack_repo)
    sub = _add_submodule(stack_repo, tmp_path)
    git("update-ref", "refs/remotes/origin/main", "HEAD", cwd=stack_repo)
    for n in (1, 2):
        (stack_repo / f"on-top-{n}.txt").write_text(f"{n}\n", encoding="utf-8")
        git("add", f"on-top-{n}.txt", cwd=stack_repo)
        git("commit", "-m", f"on top {n}", cwd=stack_repo)
    stack = get_stack_snapshot(stack_repo, settings=Settings.from_cwd(stack_repo)).commits
    assert [c.subject for c in stack] == ["on top 1", "on top 2"]
    _stage_another_submodule_commit(stack_repo, sub)
    head = git("rev-parse", "HEAD", cwd=stack_repo).stdout.strip()

    code, _out, err = run_cli(stack_repo, main_fn, argv, monkeypatch, gerrit=ChangeStore({}))
    assert (code, err.strip()) == (ExitCode.USAGE, f"error: {_STAGED_LIB}")
    assert git("rev-parse", "HEAD", cwd=stack_repo).stdout.strip() == head
    assert not (stack_repo / ".git" / "rebase-merge").exists()


def test_reorder_refuses_a_staged_submodule_even_with_autostash(
    stack_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Autostash would not get it out of the way, so the refusal is not ``uncommitted-changes``."""
    _configure(stack_repo)
    sub = _add_submodule(stack_repo, tmp_path)
    _stage_another_submodule_commit(stack_repo, sub)
    head = git("rev-parse", "HEAD", cwd=stack_repo).stdout.strip()
    settings = Settings.from_cwd(stack_repo)
    *below, top = get_stack_snapshot(stack_repo, settings=settings).commits
    steps = [*(StackStep(c.sha) for c in below), StackStep(top.sha, "drop")]
    with pytest.raises(ReorderRefusal, match="staged for lib") as refused:
        rewrite_stack(stack_repo, steps, settings=settings, autostash=True)
    assert not isinstance(refused.value, UncommittedChanges)
    assert git("rev-parse", "HEAD", cwd=stack_repo).stdout.strip() == head


def test_unknown_command_suggests_the_close_one(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, _out, err = run_cli(stack_repo, ger_main, ["lgo"], monkeypatch)
    assert code == ExitCode.USAGE
    assert "error: unknown command 'lgo'; did you mean 'log'?" in err


def test_ready_boundary_rev_is_a_usage_error_that_names_the_way_out(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``stack_repo``'s third commit matches the stop pattern, so ``--all`` would push past it."""
    _configure(stack_repo)
    code, _out, err = run_cli(stack_repo, push_main, ["--dry-run", "HEAD"], monkeypatch)
    assert code == ExitCode.USAGE
    assert "is at or after the ready boundary" in err
    assert "or pass --all to push past it" in err
    assert "\\\\" not in err, "the stop pattern is quoted as written, not repr()-escaped"


def test_errors_come_after_what_the_command_printed(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-TTY push without -y: the listing, then the reason it stopped."""
    import io
    import sys

    _configure(stack_repo)
    shared = io.StringIO()

    class _Tee(io.StringIO):
        def write(self, s: str) -> int:
            return shared.write(s)

    monkeypatch.chdir(stack_repo)
    monkeypatch.setattr(sys, "stdout", _Tee())
    monkeypatch.setattr(sys, "stderr", _Tee())
    code = push_main([], gerrit=ChangeStore({}))
    text = shared.getvalue()
    assert code == ExitCode.USAGE
    assert text.rstrip().endswith("error: non-interactive stdin: use --yes (-y) to push without a confirmation prompt")
    assert "About to push" in text


def test_log_and_push_agree_on_an_unsquashed_fixup(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One classification: the commit ``ger push`` stops at is the one ``ger log`` names, in the same words."""
    _configure(stack_repo)
    git("config", "gerrit.stopPattern", "^nomatch$", cwd=stack_repo)
    git("commit", "--allow-empty", "-q", "-m", "fixup! whatever", cwd=stack_repo)

    _code, log_out, _err = run_cli(stack_repo, log_main, [], monkeypatch, gerrit=ChangeStore({}))
    _code, push_out, push_err = run_cli(stack_repo, push_main, ["--dry-run"], monkeypatch, gerrit=ChangeStore({}))

    fixup_line = next(line for line in log_out.splitlines() if "fixup! whatever" in line)
    assert fixup_line.rstrip().endswith("# unsquashed fixup commit")
    assert "because it is an unsquashed fixup commit" in push_out + push_err
    # The plain-text boundary marker sits right above the commit push stops at.
    lines = log_out.splitlines()
    assert lines[lines.index(fixup_line) - 1].startswith("---- ready boundary")


def test_log_push_and_change_id_agree_on_an_invalid_change_id(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A garbage footer is *invalid*, not missing: ``ger log`` says so in the words push and change-id use."""
    _configure(stack_repo)
    git("config", "gerrit.stopPattern", "^nomatch$", cwd=stack_repo)
    git("commit", "--allow-empty", "-q", "--no-verify", "-m", "garbage footer\n\nChange-Id: Ibad", cwd=stack_repo)

    _code, log_out, _err = run_cli(stack_repo, log_main, [], monkeypatch, gerrit=ChangeStore({}))
    _code, json_out, _err = run_cli(stack_repo, log_main, ["--json"], monkeypatch, gerrit=ChangeStore({}))
    _code, push_out, push_err = run_cli(stack_repo, push_main, ["--dry-run"], monkeypatch, gerrit=ChangeStore({}))
    _code, _out, cid_err = run_cli(stack_repo, change_id_main, ["--check"], monkeypatch)

    line = next(line for line in log_out.splitlines() if "garbage footer" in line)
    assert line.rstrip().endswith("# invalid Change-Id")
    row = next(c for c in json.loads(json_out)["commits"] if c["summary"] == "garbage footer")
    assert row["attention_reasons"] == ["invalid-change-id"]
    assert "invalid Change-Id: 'Ibad'" in push_out + push_err
    assert "invalid Change-Id: 'Ibad'" in cid_err


def _stop_on_a_conflict(repo: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Two stack commits growing one file; dropping the first stops the second on a conflict."""
    _configure(repo)
    for text, subject in (("one\n", "grow: one"), ("one\ntwo\n", "grow: two"), ("one\ntwo\nthree\n", "grow: three")):
        (repo / "grow.txt").write_text(text, encoding="utf-8")
        git("add", "grow.txt", cwd=repo)
        git("commit", "-q", "-m", subject, cwd=repo)
    _code, _out, err = run_cli(repo, edit_main, ["--drop", "HEAD~1"], monkeypatch, gerrit=ChangeStore({}))
    assert git("diff", "--name-only", "--diff-filter=U", cwd=repo).stdout.split() == ["grow.txt"]
    return err


def test_a_conflict_in_an_attention_walk_names_finishing_and_walking_on_apart(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``git rebase --continue`` only finishes; ``ger edit -n`` is named as the way on to the next stop."""
    _stop_on_a_conflict(stack_repo, monkeypatch)
    _code, _out, err = run_cli(stack_repo, edit_main, ["-n"], monkeypatch, gerrit=ChangeStore({}))
    assert err.rstrip().endswith(
        "next: resolve the conflicts in grow.txt, `git add` them, then `git rebase --continue` to finish; "
        "`ger edit -n` finishes and goes on to the next commit needing attention; "
        "`git rebase --abort` goes back to where the rebase started"
    )


def test_a_rebase_stopped_on_a_conflict_says_how_on_and_nothing_commits_or_pushes_it(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The next step names resolving and ``git add``; ``ger fix`` and ``ger push`` refuse until then."""
    err = _stop_on_a_conflict(stack_repo, monkeypatch)
    step = (
        "resolve the conflicts in grow.txt, `git add` them, then `git rebase --continue` to finish; "
        "`git rebase --abort` goes back to where the rebase started"
    )
    assert err.rstrip().endswith(f"next: {step}")
    head = git("rev-parse", "HEAD", cwd=stack_repo).stdout

    code, _out, fix_err = run_cli(stack_repo, fix_main, ["-a", "HEAD"], monkeypatch)
    assert (code, fix_err.strip()) == (
        ExitCode.USAGE,
        f"error: the index has unresolved conflicts, and a fixup would commit them; {step}",
    )
    assert git("rev-parse", "HEAD", cwd=stack_repo).stdout == head

    for argv in (["-y"], ["--dry-run"]):
        code, _out, push_err = run_cli(stack_repo, push_main, argv, monkeypatch, gerrit=ChangeStore({}))
        assert code == ExitCode.USAGE
        assert "a rebase is in progress, and 'feature' still points at the commits from before it" in push_err
