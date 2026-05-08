"""Utilities for invoking git commands with consistent error handling."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Cap logged stdout/stderr so huge blobs (e.g. binary mis-invocation) do not flood the log.
_LOG_IO_CAP = 1000


class GitError(RuntimeError):
    """Git command failed."""

    def __init__(self, message: str, *, stderr: str = "", returncode: int = -1) -> None:
        super().__init__(message)
        self.stderr = stderr
        self.returncode = returncode

    def __str__(self) -> str:
        return f"{self.args[0]}\n{self.stderr.strip()}"


def git(
    *args: str,
    cwd: Path | str | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run git with given args; cwd defaults to current directory."""
    cmd = ("git", *args)
    cwd_str = str(cwd) if cwd is not None else None
    logger.debug("run: %s (cwd=%s)", " ".join(cmd), cwd_str or ".")
    merged = {**os.environ, **env} if env else None
    p = subprocess.run(
        cmd,
        cwd=cwd,
        env=merged,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and p.returncode != 0:
        raise GitError(
            f"git {' '.join(args)} failed: {p.stderr.strip() or p.stdout.strip()}",
            stderr=p.stderr,
            returncode=p.returncode,
        )
    return p


def git_out(*args: str, cwd: Path | str | None = None) -> str:
    """Return stripped stdout from git."""
    return git(*args, cwd=cwd).stdout.strip()
