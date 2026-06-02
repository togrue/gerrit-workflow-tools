"""Build local commit chains in a cloned Gerrit repo (integration tests)."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from gerrit_workflow_tools.core.git_run import git, git_out
from tests.integration.gerrit_seed import set_origin_url

_commit_msg_hook_cache: dict[str, Path] = {}


def install_commit_msg_hook(repo: Path, *, http_base: str) -> None:
    """Install Gerrit ``commit-msg`` hook (download once per ``http_base`` per pytest process)."""
    hook = repo / ".git" / "hooks" / "commit-msg"
    hook.parent.mkdir(parents=True, exist_ok=True)
    base = http_base.rstrip("/")
    cached = _commit_msg_hook_cache.get(base)
    if cached is None:
        url = f"{base}/tools/hooks/commit-msg"
        cache_dir = Path(tempfile.gettempdir()) / "gerrit-workflow-tools-hooks"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached = cache_dir / "commit-msg"
        for cmd in (
            ["curl", "-sfL", "-o", str(cached), url],
            ["wget", "-q", "-O", str(cached), url],
        ):
            try:
                subprocess.run(cmd, check=True, timeout=60)
                break
            except (OSError, subprocess.CalledProcessError):
                continue
        else:
            raise RuntimeError("Could not download commit-msg hook (need curl or wget on PATH)")
        cached.chmod(cached.stat().st_mode | 0o111)
        _commit_msg_hook_cache[base] = cached
    shutil.copy2(cached, hook)
    hook.chmod(hook.stat().st_mode | 0o111)


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
    user_email = f"{git_user}@example.com"
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Dev User",
        "GIT_AUTHOR_EMAIL": user_email,
        "GIT_COMMITTER_NAME": "Dev User",
        "GIT_COMMITTER_EMAIL": user_email,
    }
    # Seed copy already has local branches; skip network fetch after retargeting origin.
    p = git("checkout", branch, cwd=dest, env=env, check=False)
    if p.returncode != 0:
        p2 = git("checkout", "-b", branch, f"origin/{branch}", cwd=dest, env=env, check=False)
        if p2.returncode != 0:
            git("checkout", "-b", branch, branch, cwd=dest, env=env)
    git("config", "user.name", "Dev User", cwd=dest)
    git("config", "user.email", user_email, cwd=dest)
    return dest


def build_linear_chain(
    repo: Path,
    subjects: list[str],
    *,
    env: dict[str, str] | None = None,
) -> list[str]:
    """Create one commit per subject; subjects are full commit messages (hook adds Change-Id)."""
    shas: list[str] = []
    for i, msg in enumerate(subjects):
        fname = f"chain_{i}.txt"
        (repo / fname).write_text(f"{msg}\n", encoding="utf-8")
        git("add", fname, cwd=repo, env=env)
        git("commit", "-m", msg, cwd=repo, env=env)
        shas.append(git_out("rev-parse", "HEAD", cwd=repo).strip())
    return shas
