"""Build local commit chains in a cloned Gerrit repo (integration tests)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from gerrit_workflow_tools.core.git_run import git, git_out
from tests.integration.gerrit_seed import set_origin_url


def install_commit_msg_hook(repo: Path, *, http_base: str) -> None:
    """Download Gerrit ``commit-msg`` hook from the server."""
    hook = repo / ".git" / "hooks" / "commit-msg"
    hook.parent.mkdir(parents=True, exist_ok=True)
    url = f"{http_base.rstrip('/')}/tools/hooks/commit-msg"
    for cmd in (
        ["curl", "-sfL", "-o", str(hook), url],
        ["wget", "-q", "-O", str(hook), url],
    ):
        try:
            subprocess.run(cmd, check=True, timeout=60)
            break
        except (OSError, subprocess.CalledProcessError):
            continue
    else:
        raise RuntimeError("Could not download commit-msg hook (need curl or wget on PATH)")
    mode = hook.stat().st_mode
    hook.chmod(mode | 0o111)


def prepare_worktree_clone(
    *,
    source_seed_repo: Path,
    dest: Path,
    branch: str,
    http_base: str,
    project: str,
    git_user: str,
    git_password: str,
) -> Path:
    """Copy a seeded template repo, point origin at *git_user*, checkout *branch*."""
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(source_seed_repo, dest, symlinks=True)
    set_origin_url(
        dest,
        http_base=http_base,
        user=git_user,
        password=git_password,
        project=project,
    )
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Dev User",
        "GIT_AUTHOR_EMAIL": "devuser@test.example",
        "GIT_COMMITTER_NAME": "Dev User",
        "GIT_COMMITTER_EMAIL": "devuser@test.example",
    }
    git("fetch", "origin", cwd=dest, env=env)
    p = git("checkout", branch, cwd=dest, env=env, check=False)
    if p.returncode != 0:
        git("checkout", "-b", branch, f"origin/{branch}", cwd=dest, env=env)
    return dest


def build_linear_chain(
    repo: Path,
    subjects: list[str],
    *,
    env: dict[str, str] | None = None,
) -> list[str]:
    """Create one commit per subject; subjects are full commit messages (hook adds Change-Id)."""
    env = env or {
        **os.environ,
        "GIT_AUTHOR_NAME": "Dev User",
        "GIT_AUTHOR_EMAIL": "devuser@test.example",
        "GIT_COMMITTER_NAME": "Dev User",
        "GIT_COMMITTER_EMAIL": "devuser@test.example",
    }
    shas: list[str] = []
    for i, msg in enumerate(subjects):
        fname = f"chain_{i}.txt"
        (repo / fname).write_text(f"{msg}\n", encoding="utf-8")
        git("add", fname, cwd=repo, env=env)
        git("commit", "-m", msg, cwd=repo, env=env)
        shas.append(git_out("rev-parse", "HEAD", cwd=repo).strip())
    return shas
