# Spec: docu/spec/commands/checkout.md, docu/spec/commands/review.md
"""What a submodule holds of yours, as ger checkout and ger review see it (#41).

A submodule's own state (edits inside it, another commit checked out) is nothing switching or
rebasing touches, so it never stops them. A submodule commit staged in the index is in the way,
and is refused with a message naming the submodule and the command that clears it.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_common import ExitCode
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
