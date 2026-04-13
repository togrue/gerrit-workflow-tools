from __future__ import annotations


from gerrit_workflow_tools.cli_gready import main as gready_main
from tests.conftest import json_stdout, run_cli


def test_gready_default_stops_at_test_bang(stack_repo, monkeypatch):
    code, out, err = run_cli(stack_repo, gready_main, ["--json"], monkeypatch)
    assert code == 0
    data = json_stdout(out)
    assert data["pushable_commits"] == 2
    assert data["boundary_commit"] is not None
    assert "test" in data["boundary_reason"].lower()
    assert data["push_range"] is not None
    mb, tip = data["push_range"].split("..", 1)
    assert mb == data["merge_base"]
    assert len(tip) == 40


def test_gready_all_ignores_boundary(stack_repo, monkeypatch):
    code, out, err = run_cli(stack_repo, gready_main, ["--json", "--all"], monkeypatch)
    assert code == 0
    data = json_stdout(out)
    assert data["pushable_commits"] == 4
    assert data["boundary_commit"] is None


def test_gready_ignore_pattern_removes_test_stop(stack_repo, monkeypatch):
    code, out, err = run_cli(
        stack_repo,
        gready_main,
        ["--json", "--ignore-pattern", r"^test!"],
        monkeypatch,
    )
    assert code == 0
    data = json_stdout(out)
    assert data["pushable_commits"] == 4


def test_gready_no_config_patterns(stack_repo, monkeypatch):
    code, out, err = run_cli(stack_repo, gready_main, ["--json", "--no-config-patterns"], monkeypatch)
    assert code == 0
    data = json_stdout(out)
    assert data["pushable_commits"] == 4
