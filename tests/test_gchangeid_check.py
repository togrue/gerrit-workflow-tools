from __future__ import annotations


from gerrit_workflow_tools.cli_gchangeid_check import main as gchangeid_main
from tests.conftest import json_stdout, run_cli


def test_changeid_ok_on_stack(stack_repo, monkeypatch):
    code, out, err = run_cli(stack_repo, gchangeid_main, [], monkeypatch)
    assert code == 0
    assert "OK" in out


def test_changeid_ok_json(stack_repo, monkeypatch):
    code, out, err = run_cli(stack_repo, gchangeid_main, ["--json"], monkeypatch)
    assert code == 0
    data = json_stdout(out)
    assert data["exit_code"] == 0
    assert data["issues"] == []


def test_changeid_duplicate_exits_2(dup_repo, monkeypatch):
    code, out, err = run_cli(dup_repo, gchangeid_main, [], monkeypatch)
    assert code == 2
    assert "duplicate" in err.lower()


def test_changeid_malformed_strict_exits_2(malformed_cid_repo, monkeypatch):
    code, out, err = run_cli(
        malformed_cid_repo, gchangeid_main, ["--strict"], monkeypatch
    )
    assert code == 2
    assert "malformed" in err.lower() or "invalid" in err.lower()


def test_changeid_malformed_lenient_exits_1(malformed_cid_repo, monkeypatch):
    code, out, err = run_cli(
        malformed_cid_repo, gchangeid_main, ["--lenient"], monkeypatch
    )
    assert code == 1
    assert "malformed" in err.lower() or "invalid" in err.lower()


def test_changeid_range(stack_repo, monkeypatch):
    from gerrit_workflow_tools.git_run import git_out

    mb = git_out("merge-base", "HEAD", "main", cwd=stack_repo)
    code, out, err = run_cli(
        stack_repo,
        gchangeid_main,
        ["--range", f"{mb}..HEAD"],
        monkeypatch,
    )
    assert code == 0
