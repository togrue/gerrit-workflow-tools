# Spec: docu/spec/commands/checkout.md, docu/spec/commands/review.md, docu/spec/commands/edit.md
"""What a submodule holds of yours, as ger checkout, ger review and ger edit see it (#41).

A submodule's own state (edits inside it, another commit checked out, its directory gone) is
nothing switching or rebasing touches, so it never stops them. A submodule commit staged in the
index, or a file or symlink put where a submodule is, is in the way and nothing ``git stash``
saves: it is refused with a message naming the submodule and what clears it.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_checkout import main as checkout_main
from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_edit import main as edit_main
from gerrit_workflow_tools.cli_review import main as review_main
from gerrit_workflow_tools.core.gerrit_patchset import changes_ref
from gerrit_workflow_tools.core.git_run import git, git_out
from tests.change_store import ChangeStore
from tests.cli_gerrit_mocks import build_details_by_change_id, stack_rows_mb_to_head
from tests.conftest import run_cli
from tests.helpers import add_submodule


WEB = "https://g.example"


@dataclass
class SubStack:
    """The stack as changes 100-103 on Gerrit, over a base that records submodule ``lib``."""

    repo: Path
    store: ChangeStore
    shas: list[str]


@pytest.fixture
def sub_stack(stack_repo: Path, tmp_path: Path) -> SubStack:
    git("config", "gerrit.webUrl", WEB, cwd=stack_repo)
    git("switch", "-q", "main", cwd=stack_repo)
    add_submodule(stack_repo, tmp_path)
    git("update-ref", "refs/remotes/origin/main", "main", cwd=stack_repo)
    git("switch", "-q", "feature", cwd=stack_repo)
    git("rebase", "-q", "main", cwd=stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    store = ChangeStore(details, web_base=WEB)
    # 'origin' is the repository itself ('.'), so the change refs only have to exist here.
    for number, row in enumerate(rows, start=100):
        git("update-ref", changes_ref(number, 1), row.sha, cwd=stack_repo)
    related = [{"_change_number": 100 + i, "_revision_number": 1, "status": "NEW"} for i in range(len(rows))]
    for key in details:
        store.set_related(key, list(reversed(related)))
    return SubStack(stack_repo, store, [row.sha for row in rows])


def _review(cwd: Path, s: SubStack, argv: list[str], monkeypatch: pytest.MonkeyPatch) -> tuple[int, str, str]:
    return run_cli(cwd, review_main, argv, monkeypatch, gerrit=s.store, catch_sys_exit=True)


def _review_worktree_with_lib(s: SubStack, monkeypatch: pytest.MonkeyPatch) -> Path:
    """``ger review 102``, staged in the review worktree, its submodule checked out there."""
    assert _review(s.repo, s, ["102"], monkeypatch)[0] == ExitCode.OK
    worktree = s.repo.parent / f"{s.repo.name}-review"
    git("-c", "protocol.file.allow=always", "submodule", "update", "-q", "--init", cwd=worktree)
    return worktree


def test_a_submodule_at_another_commit_with_edits_inside_never_stops_a_staged_review(
    sub_stack: SubStack, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree = _review_worktree_with_lib(sub_stack, monkeypatch)
    lib = worktree / "lib"
    git("checkout", "-q", "HEAD~1", cwd=lib)
    (lib / "a.txt").write_text("dirty\n", encoding="utf-8")
    (lib / "untracked.txt").write_text("new\n", encoding="utf-8")
    lib_head = git_out("rev-parse", "HEAD", cwd=lib)

    for argv in (["--unstage"], ["--stage"], ["--next"], ["--done"]):
        code, _, err = _review(worktree, sub_stack, argv, monkeypatch)
        assert code == ExitCode.OK, (argv, err)
    assert git_out("rev-parse", "HEAD", cwd=lib) == lib_head
    assert (lib / "a.txt").read_text(encoding="utf-8") == "dirty\n"
    assert (lib / "untracked.txt").read_text(encoding="utf-8") == "new\n"


def test_a_submodule_staged_on_a_staged_review_refuses_with_the_command_that_unstages_it(
    sub_stack: SubStack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Staged, the index holds the change over its parent: committing would commit the review too,
    so only the restore step is given, from the change's commit."""
    worktree = _review_worktree_with_lib(sub_stack, monkeypatch)
    git("checkout", "-q", "HEAD~1", cwd=worktree / "lib")
    git("add", "lib", cwd=worktree)
    change = sub_stack.shas[2][:12]

    for argv, rerun in ((["--next"], "ger review"), (["--done"], "ger review --done")):
        code, _, err = _review(worktree, sub_stack, argv, monkeypatch)
        assert (code, err.strip()) == (
            ExitCode.USAGE,
            f"error: the submodule commit staged for lib is not the one {change} records; "
            f"`git restore --staged --source={change} lib`, then run {rerun} again",
        )
    command = err.split("`")[1]
    subprocess.run(["sh", "-c", command], cwd=worktree, check=True)
    assert _review(worktree, sub_stack, ["--next"], monkeypatch)[0] == ExitCode.OK


def _edit_inside(lib: Path) -> None:
    (lib / "a.txt").write_text("dirty\n", encoding="utf-8")


def _untracked_inside(lib: Path) -> None:
    (lib / "untracked.txt").write_text("new\n", encoding="utf-8")


def _at_another_commit(lib: Path) -> None:
    git("checkout", "-q", "HEAD~1", cwd=lib)


def _directory_gone(lib: Path) -> None:
    shutil.rmtree(lib)


def _staged_at_another_commit(lib: Path) -> None:
    git("checkout", "-q", "HEAD~1", cwd=lib)
    git("add", "lib", cwd=lib.parent)


def _replaced_by_a_file(lib: Path) -> None:
    shutil.rmtree(lib)
    lib.write_text("precious\n", encoding="utf-8")


def _replaced_by_a_symlink(lib: Path) -> None:
    shutil.rmtree(lib)
    lib.symlink_to("a.txt")


def _replaced_by_a_file_and_staged(lib: Path) -> None:
    _replaced_by_a_file(lib)
    git("add", "lib", cwd=lib.parent)


_REPLACED = (
    "a file or symlink replaces the submodule at lib; `git stash` does not save it and a switch or rebase "
    "would delete it: commit it (`git add lib`, then `git commit`) or move it out of the way"
)
_STAGED = "the submodule commit staged for lib is not committed; commit it or `git restore --staged lib`"
_COMMANDS: dict[str, tuple[Callable[..., int], list[str], str, str]] = {
    # name: (main, argv, its rerun clause, its wording for what git stash does save)
    "checkout": (checkout_main, ["102"], ", then run ger checkout again", "changes to tracked files (lib)"),
    "review": (review_main, ["102", "--in-place"], ", then run ger review again", "changes to tracked files (lib)"),
    "edit": (edit_main, ["HEAD~1"], " first", "you have uncommitted changes"),
}


@pytest.mark.parametrize("command", list(_COMMANDS))
@pytest.mark.parametrize(
    ("make", "refusal"),
    [
        (_edit_inside, None),
        (_untracked_inside, None),
        (_at_another_commit, None),
        (_directory_gone, None),
        (_staged_at_another_commit, _STAGED),
        (_replaced_by_a_file, _REPLACED),
        (_replaced_by_a_symlink, _REPLACED),
        (_replaced_by_a_file_and_staged, "stash"),
    ],
)
def test_checkout_review_and_edit_agree_on_what_a_submodule_holds_of_yours(
    command: str,
    make: Callable[[Path], None],
    refusal: str | None,
    sub_stack: SubStack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One check decides, whichever command asks. A file where a submodule was is what a switch or
    rebase would delete unasked: ``git stash`` does not save it, so the advice is to commit it."""
    main_fn, argv, rerun, stash_wording = _COMMANDS[command]
    repo = sub_stack.repo
    if command != "edit":  # as after a clone: the change comes from Gerrit
        git("switch", "-q", "main", cwd=repo)
        git("branch", "-q", "-D", "feature", cwd=repo)
    lib = repo / "lib"
    make(lib)
    head = git_out("rev-parse", "HEAD", cwd=repo)
    replaced = lib.is_symlink() or lib.is_file()
    kept = (lib.is_symlink() and str(lib.readlink())) or (lib.is_file() and lib.read_text(encoding="utf-8"))

    code, _out, err = run_cli(repo, main_fn, argv, monkeypatch, gerrit=sub_stack.store, catch_sys_exit=True)
    if refusal is None:
        assert code == ExitCode.OK, err
        return
    if command == "review" and lib.is_symlink():
        # ger review fetches before it looks, and git's fetch (recursing into submodules) refuses first.
        assert code == ExitCode.GIT and "expected submodule path 'lib' not to be a symbolic link" in err, err
    elif refusal == "stash":  # staged, the file is a change git stash saves: the usual wording
        assert code == ExitCode.USAGE and stash_wording in err, err
    else:
        why = " (a rebase would stop on it, or fold it into the commit it stops at)" if refusal == _STAGED else ""
        assert (code, err.strip()) == (ExitCode.USAGE, f"error: {refusal}{rerun}{why if command == 'edit' else ''}")
    assert git_out("rev-parse", "HEAD", cwd=repo) == head
    if replaced:
        assert ((lib.is_symlink() and str(lib.readlink())) or lib.read_text(encoding="utf-8")) == kept


def test_a_file_replacing_a_submodule_in_a_staged_review_is_not_to_be_committed(
    sub_stack: SubStack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Committing would commit the change under review with it: only moving it away is advised."""
    worktree = _review_worktree_with_lib(sub_stack, monkeypatch)
    _replaced_by_a_file(worktree / "lib")
    code, _, err = _review(worktree, sub_stack, ["--next"], monkeypatch)
    assert (code, err.strip()) == (
        ExitCode.USAGE,
        "error: a file or symlink replaces the submodule at lib; `git stash` does not save it and a switch "
        "or rebase would delete it: move it out of the way, then run ger review again",
    )
    assert (worktree / "lib").read_text(encoding="utf-8") == "precious\n"
