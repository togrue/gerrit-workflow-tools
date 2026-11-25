"""A negative vote stops the attention walk, and every shell says so the same way.

``ger edit`` stops there and says whose vote it is, ``ger show --first-attention`` finds the same
commit, ``GET /stack`` marks it ``needs_edit`` and the agent runbook leaves it to a human. Once the
commit is amended with other code, the vote is on a patch set it replaced: the walk goes past it,
as it does past a failed build on such a patch set.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_edit import main as edit_main
from gerrit_workflow_tools.cli_show import main as show_main
from gerrit_workflow_tools.core.git_run import git
from gerrit_workflow_tools.server.api import GerApi
from tests.conftest import json_stdout, run_cli
from tests.helpers import plain_git
from tests.test_serve import V1, _gerrit


def _no_rebase(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stand in for ``git rebase``: the walk's choice is what is under test, not the rebase."""
    stopped: list[str] = []
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        if plain_git(cmd)[:2] == ["git", "rebase"]:
            stopped.append(kwargs["env"]["GER_EDIT_SHORT_SHA"])
            return subprocess.CompletedProcess(cmd, 0)
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)
    return stopped


def test_a_negative_vote_stops_the_walk_until_the_commit_is_amended(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _keys, rows = _gerrit(stack_repo, [{}, {}, {}, {"cr": -1, "cr_by": "Bob"}])
    voted = rows[3]

    code, out, err = run_cli(stack_repo, show_main, ["--first-attention", "--json"], monkeypatch, gerrit=store)
    assert code == 0, err
    assert json_stdout(out)["sha"] == voted.sha

    api = GerApi(stack_repo, gerrit=store)
    stack = api.handle("GET", f"{V1}/stack", {})
    assert [c["needs_edit"] for c in stack["commits"]] == [False, False, False, True]

    runbook = api.handle("GET", f"{V1}/agent/runbook", {})
    assert [(c["sha"], c["agent_work"]) for c in runbook["commits"]] == [(voted.sha, False)]
    assert f"{voted.short_sha} {voted.subject} — Code-Review -1 from Bob: a vote is for a human" in runbook["markdown"]

    stopped = _no_rebase(monkeypatch)
    code, _out, err = run_cli(stack_repo, edit_main, ["--first-attention-commit"], monkeypatch, gerrit=store)
    assert code == 0, err
    assert stopped == [voted.short_sha]
    assert f"{voted.short_sha} {voted.subject} (oldest needing attention: Code-Review -1 from Bob)" in err

    # New code, push pending: Bob voted on the patch set this commit replaced.
    (stack_repo / "more.txt").write_text("more code\n", encoding="utf-8")
    git("add", "-A", cwd=stack_repo)
    git("commit", "-q", "--amend", "--no-edit", cwd=stack_repo)
    code, _out, err = run_cli(stack_repo, show_main, ["--first-attention"], monkeypatch, gerrit=store)
    assert code != 0
    assert "no commit needs edit attention" in err
    assert not any(c["needs_edit"] for c in api.handle("GET", f"{V1}/stack", {})["commits"])
