"""Utilities for invoking git commands with consistent error handling."""

from __future__ import annotations

import logging
import os
import subprocess
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

# Cap logged stdout/stderr so huge blobs (e.g. binary mis-invocation) do not flood the log.
_LOG_IO_CAP = 1000

# Read-only subcommands safe to cache for the lifetime of a single CLI run.
_CACHEABLE_SUBCOMMANDS = frozenset({"rev-parse", "log"})


class GitError(RuntimeError):
    """Git command failed."""

    def __init__(self, message: str, *, stderr: str = "", returncode: int = -1) -> None:
        super().__init__(message)
        self.stderr = stderr
        self.returncode = returncode

    def __str__(self) -> str:
        return f"{self.args[0]}\n{self.stderr.strip()}"


def clear_git_cache() -> None:
    """Drop all cached ``rev-parse`` / ``log`` results (mainly for tests)."""
    _git_cached.cache_clear()


def _resolve_cwd(cwd: Path | str | None) -> str:
    if cwd is not None:
        return os.path.abspath(os.path.expanduser(str(cwd)))
    return os.getcwd()


def _run_git(
    *args: str,
    cwd: Path | str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    cmd = ("git", *args)
    cwd_str = str(cwd) if cwd is not None else None
    logger.debug("run: %s (cwd=%s)", " ".join(cmd), cwd_str or ".")
    merged = {**os.environ, **env} if env else None
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=merged,
        text=True,
        capture_output=True,
        check=False,
    )


@lru_cache(maxsize=512)
def _git_cached(
    args: tuple[str, ...],
    cwd_abs: str,
    env_key: tuple[tuple[str, str], ...] | None,
) -> subprocess.CompletedProcess[str]:
    env = dict(env_key) if env_key else None
    p = _run_git(*args, cwd=cwd_abs, env=env)
    if p.returncode != 0:
        _git_cached.cache_clear()
    return p


def _raise_if_failed(
    args: tuple[str, ...],
    p: subprocess.CompletedProcess[str],
    *,
    check: bool,
) -> None:
    if check and p.returncode != 0:
        raise GitError(
            f"git {' '.join(args)} failed: {p.stderr.strip() or p.stdout.strip()}",
            stderr=p.stderr,
            returncode=p.returncode,
        )


def git(
    *args: str,
    cwd: Path | str | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run git with given args; cwd defaults to current directory."""
    if args and args[0] in _CACHEABLE_SUBCOMMANDS:
        env_key = tuple(sorted(env.items())) if env else None
        p = _git_cached(args, _resolve_cwd(cwd), env_key)
    else:
        _git_cached.cache_clear()
        p = _run_git(*args, cwd=cwd, env=env)
    _raise_if_failed(args, p, check=check)
    return p


def git_out(
    *args: str,
    cwd: Path | str | None = None,
    env: dict[str, str] | None = None,
) -> str:
    """Return stripped stdout from git."""
    return git(*args, cwd=cwd, env=env).stdout.strip()
