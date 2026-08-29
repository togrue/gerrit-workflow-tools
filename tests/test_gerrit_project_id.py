from __future__ import annotations

import pytest

from gerrit_workflow_tools.core.config import Settings
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
        ("http://dev:secret@lenovo-pc:8081/it_v_abc", "it_v_abc"),
        ("user@gerrit.example.com:group/proj.git", "group/proj"),
        ("user@gerrit.example.com:a/group/proj", "group/proj"),
        ("user@lenovo-pc:29418/test-git-graph-repo", "test-git-graph-repo"),
        ("", None),
    ],
)
def test_parse_project_name_from_remote_url(remote_url: str, expected: str | None) -> None:
    assert parse_project_name_from_remote_url(remote_url) == expected


def test_resolve_gerrit_project_name_prefers_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("gerrit_workflow_tools.core.gerrit_project_id.git_out", lambda *a, **k: "unused")
    settings = Settings.from_map({"gerrit.project": "cfg/proj"})
    assert resolve_gerrit_project_name(None, settings=settings) == "cfg/proj"


def test_resolve_gerrit_project_name_from_remote_url() -> None:
    settings = Settings.from_map({"remote.origin.url": "ssh://user@gerrit.example.com/a/team/my-project.git"})
    assert resolve_gerrit_project_name(None, settings=settings) == "team/my-project"
