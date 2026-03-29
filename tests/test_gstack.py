from __future__ import annotations

import json

from gerrit_workflow_tools.cli_gstack import main as gstack_main
from tests.conftest import json_stdout, run_cli


def test_gstack_json_lists_commits_and_merge_base(stack_repo, monkeypatch):
    code, out, err = run_cli(stack_repo, gstack_main, ["--json"], monkeypatch)
    assert code == 0
    assert err == ""
    data = json_stdout(out)
    assert "merge_base" in data
    assert data["target_review_branch"] == "main"
    commits = data["commits"]
    assert len(commits) == 4
    assert commits[0]["subject"] == "Refactor parser init"
    assert commits[1]["subject"] == "Extract command routing"
    assert commits[2]["subject"] == "test! temporary experiment"
    assert commits[3]["subject"] == "Cleanup after experiment"
    assert commits[0]["change_id"].startswith("I")
    assert all(c["ready_state"] == "ready" for c in commits)


def test_gstack_with_ready_state_marks_blocked_and_after(stack_repo, monkeypatch):
    code, out, err = run_cli(
        stack_repo, gstack_main, ["--json", "--with-ready-state"], monkeypatch
    )
    assert code == 0
    data = json.loads(out)
    states = [c["ready_state"] for c in data["commits"]]
    assert states[0] == "ready"
    assert states[1] == "ready"
    assert states[2].startswith("blocked(")
    assert states[3] == "after-blocked"


def test_gstack_text_contains_subjects(stack_repo, monkeypatch):
    code, out, err = run_cli(stack_repo, gstack_main, [], monkeypatch)
    assert code == 0
    assert "Refactor parser init" in out
    assert "Merge base:" in out
