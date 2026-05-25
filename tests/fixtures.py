"""Create reproducible local git repositories for tests."""

from __future__ import annotations

from pathlib import Path

from gerrit_workflow_tools.core.git_run import git


def _cid(seed: str) -> str:
    """40 hex digits after I (Gerrit-style)."""
    return "I" + (seed * 40)[:40]


# Commits built by :func:`make_gcid_cli_repo` (root .. tip).
GCID_CLI_CHANGE_IDS = (_cid("a"), _cid("b"), _cid("c"))


def make_gcid_cli_repo(path: Path) -> Path:
    """Three linear commits on ``main``, each with a distinct Change-Id (``gcid`` CLI tests)."""
    path.mkdir(parents=True, exist_ok=True)
    env = {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    git("init", "-b", "main", cwd=path, env=env)
    for msg, cid, fname in (
        ("gcid base", GCID_CLI_CHANGE_IDS[0], "g1.txt"),
        ("gcid middle", GCID_CLI_CHANGE_IDS[1], "g2.txt"),
        ("gcid tip", GCID_CLI_CHANGE_IDS[2], "g3.txt"),
    ):
        (path / fname).write_text(f"{fname}\n", encoding="utf-8")
        git("add", fname, cwd=path, env=env)
        git("commit", "-m", f"{msg}\n\nChange-Id: {cid}", cwd=path, env=env)
    return path


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


def make_repo_with_merged_side_branch(path: Path) -> Path:
    """
    Build a repo where a side branch has been merged into a feature branch.

    Topology (oldest → newest)::

        main:    base
        side:    base → S1 → S2          (branched from main)
        feature: base → local → merge-M  (merge-M merges S2 into local)

    ``feature`` tracks ``main``.  Callers can use this to verify that
    first-parent traversal returns only ``{local, merge-M}`` (2 commits)
    while full-DAG traversal returns ``{local, S1, S2, merge-M}`` (4 commits).
    """
    path.mkdir(parents=True, exist_ok=True)
    env = {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    git("init", "-b", "main", cwd=path, env=env)
    (path / "base.txt").write_text("base\n", encoding="utf-8")
    git("add", "base.txt", cwd=path, env=env)
    git("commit", "-m", "base\n\nChange-Id: I" + "0" * 40, cwd=path, env=env)

    git("checkout", "-b", "feature", cwd=path, env=env)
    git("branch", "--set-upstream-to", "main", "feature", cwd=path, env=env, check=False)
    (path / "local.txt").write_text("local\n", encoding="utf-8")
    git("add", "local.txt", cwd=path, env=env)
    git("commit", "-m", "local work\n\nChange-Id: I" + "1" * 40, cwd=path, env=env)

    git("checkout", "main", cwd=path, env=env)
    git("checkout", "-b", "side", cwd=path, env=env)
    for i, fname in enumerate(["s1.txt", "s2.txt"], 1):
        (path / fname).write_text(f"side{i}\n", encoding="utf-8")
        git("add", fname, cwd=path, env=env)
        git("commit", "-m", f"side commit {i}\n\nChange-Id: I{str(i + 1) * 40}", cwd=path, env=env)

    git("checkout", "feature", cwd=path, env=env)
    git(
        "merge",
        "--no-ff",
        "-m",
        "Merge side branch\n\nChange-Id: I" + "4" * 40,
        "side",
        cwd=path,
        env=env,
    )
    configure_gerrit_target(path, "main")
    return path


def configure_gerrit_target(path: Path, target: str = "main") -> None:
    """Set branch upstream on ``gerrit.remote`` for Gerrit push/rebase tests."""
    from gerrit_workflow_tools.core.git_run import git_out

    branch = git_out("rev-parse", "--abbrev-ref", "HEAD", cwd=path)
    remote = "origin"
    if git("remote", "get-url", remote, cwd=path, check=False).returncode != 0:
        git("remote", "add", remote, str(path.resolve()), cwd=path, check=False)
    git("fetch", remote, cwd=path, check=False)
    upstream = target if "/" in target else f"{remote}/{target}"
    main_sha = git("rev-parse", "--verify", target, cwd=path, check=False)
    if main_sha.returncode != 0 and "/" not in target:
        main_sha = git("rev-parse", "--verify", "main", cwd=path, check=False)
    if main_sha.returncode == 0:
        git("update-ref", f"refs/remotes/{upstream}", main_sha.stdout.strip(), cwd=path, check=False)
    git("branch", "--set-upstream-to", upstream, branch, cwd=path, check=False)
