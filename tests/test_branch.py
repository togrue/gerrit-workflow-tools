from __future__ import annotations

from gerrit_workflow_tools.cli_branch import main as gbranch_main
from tests.conftest import run_cli
from tests.fixtures import configure_gerrit_target


def test_gbranch_show_after_init(stack_repo_unconfigured, monkeypatch):
    repo = stack_repo_unconfigured
    monkeypatch.chdir(repo)
    assert gbranch_main(["init", "--target", "main", "--reviewers", "alice,bob"]) == 0
    code, out, _err = run_cli(repo, gbranch_main, ["show"], monkeypatch)
    assert code == 0
    assert "feature" in out
    assert "main" in out
    assert "alice,bob" in out


def test_gbranch_init_without_target_or_reviewers_noops(stack_repo_unconfigured, monkeypatch):
    repo = stack_repo_unconfigured
    code, _out, err = run_cli(repo, gbranch_main, ["init"], monkeypatch)
    assert code == 0
    assert "nothing to set" in err.lower()


def test_gbranch_set_target(stack_repo_unconfigured, monkeypatch):
    repo = stack_repo_unconfigured
    configure_gerrit_target(repo, "main")
    code, out, _err = run_cli(repo, gbranch_main, ["set-target", "release/1.0"], monkeypatch)
    assert code == 0
    code, out, _err = run_cli(repo, gbranch_main, ["show"], monkeypatch)
    assert code == 0
    assert "release/1.0" in out


def test_gbranch_infer_upstream_yes(tmp_path, monkeypatch):
    from gerrit_workflow_tools.git_run import git as git_run
    from gerrit_workflow_tools.git_run import git_out

    bare = tmp_path / "upstream.git"
    git_run("init", "--bare", str(bare))
    repo = tmp_path / "infer"
    repo.mkdir()
    git_run("init", "-b", "main", cwd=repo)
    (repo / "f").write_text("1\n", encoding="utf-8")
    git_run("add", "f", cwd=repo)
    git_run("commit", "-m", "init", cwd=repo)
    git_run("remote", "add", "origin", str(bare), cwd=repo)
    git_run("push", "-u", "origin", "main", cwd=repo)
    git_run("checkout", "-b", "feature", cwd=repo)
    (repo / "f").write_text("2\n", encoding="utf-8")
    git_run("commit", "-am", "feat", cwd=repo)

    code, _out, err = run_cli(repo, gbranch_main, ["infer-upstream", "--yes"], monkeypatch)
    assert code == 0
    assert "origin/main" in err
    assert git_out("rev-parse", "--abbrev-ref", "@{upstream}", cwd=repo) == "origin/main"


def test_gbranch_infer_upstream_no_remotes(tmp_path, monkeypatch):
    from gerrit_workflow_tools.git_run import git as git_run

    repo = tmp_path / "noremote"
    repo.mkdir()
    git_run("init", "-b", "main", cwd=repo)
    (repo / "f").write_text("1\n", encoding="utf-8")
    git_run("add", "f", cwd=repo)
    git_run("commit", "-m", "init", cwd=repo)
    git_run("checkout", "-b", "feature", cwd=repo)

    code, _out, err = run_cli(repo, gbranch_main, ["infer-upstream", "--yes"], monkeypatch)
    assert code == 1
    assert "refs/remotes" in err.lower() or "remote-tracking" in err.lower()
