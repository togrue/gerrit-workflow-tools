"""Create reproducible local git repositories for tests."""

from __future__ import annotations

from pathlib import Path

from gerrit_workflow_tools.core.git_run import git


def _cid(seed: str) -> str:
    """40 hex digits after I (Gerrit-style)."""
    return "I" + (seed * 40)[:40]


def make_stack_repo(path: Path) -> Path:
    """
    main + feature branch with 4 commits; commit 3 matches ^test!.
    All commits have valid distinct Change-Ids except optional duplicate fixture (not used here).
    """
    path.mkdir(parents=True, exist_ok=True)
    env = {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    git("init", "-b", "main", cwd=path, env=env)
    readme = path / "README.md"
    readme.write_text("init\n", encoding="utf-8")
    git("add", "README.md", cwd=path, env=env)
    git("commit", "-m", "init", cwd=path, env=env)

    git("checkout", "-b", "feature", cwd=path, env=env)

    def commit(msg: str, change_id: str, filename: str) -> None:
        fp = path / filename
        fp.write_text(f"file {filename}\n", encoding="utf-8")
        git("add", filename, cwd=path, env=env)
        git(
            "commit",
            "-m",
            f"{msg}\n\nChange-Id: {change_id}",
            cwd=path,
            env=env,
        )

    commit("Refactor parser init", _cid("1"), "a.txt")
    commit("Extract command routing", _cid("2"), "b.txt")
    commit("test! temporary experiment", _cid("3"), "c.txt")
    commit("Cleanup after experiment", _cid("4"), "d.txt")

    # Stack commands use @{upstream}..HEAD; track local main from feature.
    git("branch", "--set-upstream-to", "main", "feature", cwd=path, env=env, check=False)

    return path


def make_repo_duplicate_change_id(path: Path) -> Path:
    """Two commits sharing the same Change-Id (invalid)."""
    path.mkdir(parents=True, exist_ok=True)
    env = {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    git("init", "-b", "main", cwd=path, env=env)
    (path / "README.md").write_text("x\n", encoding="utf-8")
    git("add", "README.md", cwd=path, env=env)
    git("commit", "-m", "init", cwd=path, env=env)
    git("checkout", "-b", "bug", cwd=path, env=env)
    cid = _cid("a")
    for i, name in enumerate(["u.txt", "v.txt"]):
        (path / name).write_text(f"{i}\n", encoding="utf-8")
        git("add", name, cwd=path, env=env)
        git("commit", "-m", f"dup {i}\n\nChange-Id: {cid}", cwd=path, env=env)
    git("branch", "--set-upstream-to", "main", "bug", cwd=path, env=env, check=False)
    return path


def make_repo_malformed_cid(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    env = {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    git("init", "-b", "main", cwd=path, env=env)
    (path / "README.md").write_text("x\n", encoding="utf-8")
    git("add", "README.md", cwd=path, env=env)
    git("commit", "-m", "init", cwd=path, env=env)
    git("checkout", "-b", "bad", cwd=path, env=env)
    (path / "x.txt").write_text("1\n", encoding="utf-8")
    git("add", "x.txt", cwd=path, env=env)
    git("commit", "-m", "bad\n\nChange-Id: not-valid", cwd=path, env=env)
    git("branch", "--set-upstream-to", "main", "bad", cwd=path, env=env, check=False)
    return path


def configure_gerrit_target(path: Path, target: str = "main") -> None:
    from gerrit_workflow_tools.config import set_branch_config
    from gerrit_workflow_tools.core.git_run import git_out

    branch = git_out("rev-parse", "--abbrev-ref", "HEAD", cwd=path)
    set_branch_config(path, branch, gerrit_target=target)
    # Local stack is @{upstream}..HEAD; align tests that set gerritTarget with a real upstream.
    p = git("rev-parse", "--verify", target, cwd=path, check=False)
    if p.returncode == 0:
        git("branch", "--set-upstream-to", target, branch, cwd=path, check=False)
