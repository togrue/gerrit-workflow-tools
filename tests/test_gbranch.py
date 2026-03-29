from __future__ import annotations

from gerrit_workflow_tools.cli_gbranch import main as gbranch_main
from tests.conftest import run_cli
from tests.fixtures import configure_gerrit_target, make_stack_repo


def test_gbranch_show_after_init(tmp_path, monkeypatch):
    repo = make_stack_repo(tmp_path / "r")
    monkeypatch.chdir(repo)
    assert gbranch_main(["init", "--target", "main", "--reviewers", "alice,bob"]) == 0
    code, out, err = run_cli(repo, gbranch_main, ["show"], monkeypatch)
    assert code == 0
    assert "feature" in out
    assert "main" in out
    assert "alice,bob" in out


def test_gbranch_init_requires_target(tmp_path, monkeypatch):
    repo = make_stack_repo(tmp_path / "r")
    code, out, err = run_cli(repo, gbranch_main, ["init"], monkeypatch)
    assert code == 1
    assert "target" in err.lower()


def test_gbranch_set_target(tmp_path, monkeypatch):
    repo = make_stack_repo(tmp_path / "r")
    configure_gerrit_target(repo, "main")
    code, out, err = run_cli(
        repo, gbranch_main, ["set-target", "release/1.0"], monkeypatch
    )
    assert code == 0
    code, out, err = run_cli(repo, gbranch_main, ["show"], monkeypatch)
    assert code == 0
    assert "release/1.0" in out
