"""Shared helpers for unit tests."""

from __future__ import annotations

import re
from pathlib import Path

from gerrit_workflow_tools.core.git_run import git, git_out

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from CLI output."""
    return _ANSI_RE.sub("", text)


def ref_exists(repo: Path, ref: str) -> bool:
    p = git("rev-parse", "--verify", ref, cwd=repo, check=False)
    return p.returncode == 0


def write_rebase_head(repo: Path, branch: str, *, state_dir: str = "rebase-merge") -> None:
    """Simulate an in-progress rebase by writing ``head-name`` under ``state_dir``."""
    git_dir = Path(git_out("rev-parse", "--git-dir", cwd=repo))
    if not git_dir.is_absolute():
        git_dir = repo / git_dir
    path = git_dir / state_dir
    path.mkdir(parents=True, exist_ok=True)
    (path / "head-name").write_text(f"refs/heads/{branch}\n", encoding="utf-8")
