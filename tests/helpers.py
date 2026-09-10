"""Shared helpers for unit tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from gerrit_workflow_tools.core.git_run import git, git_out


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


def force_zero_change_trust_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """Expire the ChangeInfo trust window so tests exercise the freshness probe."""

    from gerrit_workflow_tools.core.gerrit.service import GerritService

    orig = GerritService.from_cwd.__func__

    @classmethod
    def _from_cwd(cls, cwd, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["trust_window_seconds"] = 0
        return orig(cls, cwd, **kwargs)

    monkeypatch.setattr(GerritService, "from_cwd", _from_cwd)
