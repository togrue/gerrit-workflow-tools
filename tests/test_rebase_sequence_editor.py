from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("action", ["edit", "reword", "drop"])
def test_sequence_editor_replaces_pick_with_the_action(tmp_path: Path, monkeypatch, action: str):
    from gerrit_workflow_tools.rebase_sequence_editor import main as seq_main

    todo = tmp_path / "git-rebase-todo"
    todo.write_text(
        "pick abc1234 first subject\npick def5678 second subject\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GEDIT_SHORT_SHA", "abc1234")
    monkeypatch.setenv("GEDIT_ACTION", action)
    assert seq_main(["_", str(todo)]) == 0
    assert todo.read_text(encoding="utf-8") == f"{action} abc1234 first subject\npick def5678 second subject\n"


def test_sequence_editor_missing_sha_fails(tmp_path: Path, monkeypatch):
    from gerrit_workflow_tools.rebase_sequence_editor import main as seq_main

    todo = tmp_path / "todo"
    todo.write_text("pick aaa1111 x\n", encoding="utf-8")
    monkeypatch.setenv("GEDIT_SHORT_SHA", "notfound")
    monkeypatch.setenv("GEDIT_ACTION", "edit")
    assert seq_main(["_", str(todo)]) == 1


def test_module_invocation_like_git(tmp_path: Path):
    """Smoke: same as GIT_SEQUENCE_EDITOR would run."""
    import os

    todo = tmp_path / "todo"
    todo.write_text("pick bee1234 line\n", encoding="utf-8")
    merged = {**os.environ, "GEDIT_SHORT_SHA": "bee1234", "GEDIT_ACTION": "reword"}
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "gerrit_workflow_tools.rebase_sequence_editor",
            str(todo),
        ],
        cwd=tmp_path,
        env=merged,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0
    assert "reword bee1234" in todo.read_text(encoding="utf-8")
