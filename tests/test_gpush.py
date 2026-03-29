from __future__ import annotations

from gerrit_workflow_tools.cli_gpush import main as gpush_main
from tests.conftest import run_cli
from tests.fixtures import make_stack_repo


def test_gpush_dry_run_prints_refs_for_and_push_command(stack_repo, monkeypatch):
    code, out, err = run_cli(stack_repo, gpush_main, ["--dry-run"], monkeypatch)
    assert code == 0
    assert "refs/for/main" in out
    assert "git push" in out
    assert "Summary" in out
    assert "[dry-run]" in err


def test_gpush_requires_target(tmp_path, monkeypatch):
    repo = make_stack_repo(tmp_path / "r")
    # no configure_gerrit_target
    code, out, err = run_cli(repo, gpush_main, ["--dry-run"], monkeypatch)
    assert code == 1
    assert "Gerrit target" in err or "target" in out.lower()


def test_gpush_accepts_explicit_target_without_config(tmp_path, monkeypatch):
    repo = make_stack_repo(tmp_path / "r")
    code, out, err = run_cli(
        repo,
        gpush_main,
        ["--dry-run", "--target", "main"],
        monkeypatch,
    )
    assert code == 0
    assert "refs/for/main" in out


def test_gpush_fails_on_duplicate_change_ids(dup_repo, monkeypatch):
    code, out, err = run_cli(
        dup_repo, gpush_main, ["--dry-run", "--target", "main"], monkeypatch
    )
    assert code == 2
    assert "Change-Id" in err
