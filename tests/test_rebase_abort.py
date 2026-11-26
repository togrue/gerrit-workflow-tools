# Spec: docu/spec/commands/rebase.md
"""``ger rebase --abort`` keeps what git's abort would throw away, or names it: a fix saved and not
amended goes into a stash, a commit the rebase rewrote is named with its id."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gerrit_workflow_tools.core.git_run import git, git_out
from gerrit_workflow_tools.core.rebase_abort import abort_loss
from gerrit_workflow_tools.core.rebase_state import rebase_in_progress
from gerrit_workflow_tools.core.rebasing_stack import stopped_rebase
from gerrit_workflow_tools.server.api import GerApi
from tests.test_rebase_go_on import _rebase, _stopped_on_c_txt
from tests.test_rebasing_stack import _stop_to_edit, _store
from tests.test_serve import _get


def _discards(repo: Path) -> dict[str, Any]:
    answer = _get(GerApi(repo, gerrit=_store(repo)), "/rebase/refusal", action="abort")
    assert answer["refused"] is None
    return answer["discards"]


def test_abort_keeps_a_fix_saved_and_not_amended_in_a_stash(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """State 16/18 of the outside-the-happy-path run: the saved fix was gone after Abort, unasked."""
    _stop_to_edit(stack_repo)
    stopped = git_out("log", "-1", "--format=%h %s", cwd=stack_repo)
    (stack_repo / "c.txt").write_text("a fix saved, not amended\n", encoding="utf-8")
    discards = _discards(stack_repo)
    assert (discards["uncommitted"], discards["rewritten"], discards["asks"]) == (["c.txt"], [], False)

    code, err = _rebase(stack_repo, ["--abort"], monkeypatch)

    assert code == 0, err
    assert not rebase_in_progress(stack_repo)
    assert "kept: your changes to c.txt, not amended, in stash@{0}; `git stash apply` brings them back" in err
    assert git_out("stash", "list", "--format=%gs", cwd=stack_repo) == f"ger rebase --abort: not amended at {stopped}"
    git("stash", "apply", "-q", cwd=stack_repo, check=False)
    assert (stack_repo / "c.txt").read_text(encoding="utf-8") == "a fix saved, not amended\n"


def test_abort_names_the_commit_the_rebase_rewrote(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An amend at the stop is gone from the branch after an abort: named with its id, and a client asks first."""
    _stop_to_edit(stack_repo)
    (stack_repo / "c.txt").write_text("amended\n", encoding="utf-8")
    git("commit", "-q", "-a", "--amend", "--no-edit", cwd=stack_repo)
    amended = git_out("log", "-1", "--format=%h %s", cwd=stack_repo)
    discards = _discards(stack_repo)
    assert discards["asks"] is True and discards["uncommitted"] == []
    assert [f"{c['short_sha']} {c['summary']}" for c in discards["rewritten"]] == [amended]

    code, err = _rebase(stack_repo, ["--abort"], monkeypatch)

    assert code == 0, err
    assert f"gone from the branch: {amended}, made in this rebase; `git show {amended.split()[0]}`" in err
    assert "kept:" not in err and git_out("stash", "list", cwd=stack_repo) == ""


def test_abort_at_a_conflict_on_a_replay_loses_nothing(stack_repo: Path) -> None:
    """Commits only replayed onto the new base are no work of yours: nothing to ask about, nothing stashed."""
    _stopped_on_c_txt(stack_repo)
    progress = stopped_rebase(stack_repo)
    assert progress is not None
    loss = abort_loss(stack_repo, progress)
    assert (loss.uncommitted, loss.rewritten, loss.asks) == ((), (), False)
