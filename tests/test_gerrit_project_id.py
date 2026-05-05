from __future__ import annotations

import pytest

from gerrit_workflow_tools.core.gerrit_project_id import (
    parse_project_name_from_remote_url,
    resolve_gerrit_project_name,
)


@pytest.mark.parametrize(
    ("remote_url", "expected"),
    [
        ("ssh://user@gerrit.example.com/a/group/proj.git", "group/proj"),
        ("https://gerrit.example.com/a/group/proj", "group/proj"),
        ("https://gerrit.example.com/group/proj.git", "group/proj"),
        ("user@gerrit.example.com:group/proj.git", "group/proj"),
        ("user@gerrit.example.com:a/group/proj", "group/proj"),
        ("", None),
    ],
)
def test_parse_project_name_from_remote_url(remote_url: str, expected: str | None) -> None:
    assert parse_project_name_from_remote_url(remote_url) == expected


def test_resolve_gerrit_project_name_prefers_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("gerrit_workflow_tools.core.gerrit_project_id.gerrit_project", lambda cwd: "cfg/proj")
    monkeypatch.setattr("gerrit_workflow_tools.core.gerrit_project_id.git_out", lambda *a, **k: "unused")
    assert resolve_gerrit_project_name(None) == "cfg/proj"


def test_resolve_gerrit_project_name_from_remote_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("gerrit_workflow_tools.core.gerrit_project_id.gerrit_project", lambda cwd: None)
    monkeypatch.setattr("gerrit_workflow_tools.core.gerrit_project_id.gerrit_remote", lambda cwd: "origin")
    monkeypatch.setattr(
        "gerrit_workflow_tools.core.gerrit_project_id.git_out",
        lambda *a, **k: "ssh://user@gerrit.example.com/a/team/my-project.git",
    )
    assert resolve_gerrit_project_name(None) == "team/my-project"
