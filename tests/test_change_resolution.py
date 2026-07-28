from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from gerrit_workflow_tools.core.changeish import parse
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.change_resolution import (
    ChangeAmbiguousError,
    ChangeResolutionError,
    build_triplet,
    format_resolution_note,
    parse_changeish,
    resolve_changeish,
    resolve_stack_context,
)
from gerrit_workflow_tools.core.git_run import git, git_out
from gerrit_workflow_tools.core.git_state import effective_gerrit_destination_branch
from tests.fixtures import _cid, configure_gerrit_target

CHANGE_ID = _cid("a")
CHANGE_ID_OTHER_CASE = "I" + ("b" * 40)


def _change_row(
    *,
    number: int,
    branch: str,
    change_id: str = CHANGE_ID,
    status: str = "NEW",
    project: str = "myproject",
) -> dict[str, Any]:
    triplet = f"{project}~{branch}~{change_id}"
    return {
        "_number": number,
        "id": triplet,
        "branch": branch,
        "change_id": change_id,
        "status": status,
        "project": project,
    }


def _configure_stack_repo_project(repo: Path, project: str = "myproject") -> None:
    git("config", "gerrit.project", project, cwd=repo)


def _set_upstream_to_dev(repo: Path) -> None:
    main_sha = git_out("rev-parse", "main", cwd=repo)
    git("update-ref", "refs/remotes/origin/dev", main_sha, cwd=repo)
    git("branch", "--set-upstream-to=origin/dev", "feature", cwd=repo)


def _mock_client(rows_by_query: dict[str, list[dict[str, Any]]] | None = None) -> MagicMock:
    client = MagicMock()
    rows_by_query = rows_by_query or {}

    def query_changes(query: str, *, n: int = 25, options: list[str] | None = None) -> list[dict[str, Any]]:
        del n, options
        return list(rows_by_query.get(query, []))

    def get_change(change_key: str) -> dict[str, Any]:
        for rows in rows_by_query.values():
            for row in rows:
                if str(row.get("_number")) == change_key or row.get("id") == change_key:
                    return row
        raise AssertionError(f"unexpected get_change({change_key!r})")

    client.query_changes.side_effect = query_changes
    client.get_change.side_effect = get_change
    return client


# The changeish grammar itself is covered by tests/test_changeish.py; this module covers what
# a parsed changeish *resolves to*.


def test_triplet_build_parse_round_trip() -> None:
    triplet = build_triplet("group/proj", "main", CHANGE_ID)
    assert triplet == f"group/proj~main~{CHANGE_ID}"
    assert parse(triplet).change_id == CHANGE_ID


def test_empty_changeish_is_rejected_at_resolution() -> None:
    with pytest.raises(ChangeResolutionError, match="empty changeish"):
        parse_changeish("   ")


def test_resolve_stack_context_project_from_remote_url(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git("init", "-b", "main", cwd=repo)
    (repo / "f").write_text("x", encoding="utf-8")
    git("add", "f", cwd=repo)
    git("commit", "-m", "init", cwd=repo)
    git("remote", "add", "origin", "ssh://user@gerrit.example.com/a/team/my-project.git", cwd=repo)
    git("checkout", "-b", "feature", cwd=repo)
    configure_gerrit_target(repo, "main")

    ctx = resolve_stack_context(repo, branch="feature", settings=Settings.from_cwd(repo))

    assert ctx.project == "team/my-project"
    assert ctx.push_branch == "main"


def test_resolve_stack_context_project_override(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git("init", "-b", "main", cwd=repo)
    (repo / "f").write_text("x", encoding="utf-8")
    git("add", "f", cwd=repo)
    git("commit", "-m", "init", cwd=repo)
    git("remote", "add", "origin", "ssh://user@gerrit.example.com/a/remote/proj.git", cwd=repo)
    git("config", "gerrit.project", "cfg/override", cwd=repo)
    git("checkout", "-b", "feature", cwd=repo)
    configure_gerrit_target(repo, "main")

    ctx = resolve_stack_context(repo, branch="feature", settings=Settings.from_cwd(repo))

    assert ctx.project == "cfg/override"


def test_effective_gerrit_destination_branch_gerrit_target_override(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git("init", "-b", "main", cwd=repo)
    (repo / "f").write_text("x", encoding="utf-8")
    git("add", "f", cwd=repo)
    git("commit", "-m", "init", cwd=repo)
    git("remote", "add", "origin", "ssh://user@gerrit.example.com/a/team/proj.git", cwd=repo)
    git("checkout", "-b", "feature", cwd=repo)
    _set_upstream_to_dev(repo)
    git("config", "branch.feature.gerritTarget", "main", cwd=repo)

    assert Settings.from_cwd(repo).branch_gerrit_target("feature") == "main"
    assert effective_gerrit_destination_branch(repo, "feature", settings=Settings.from_cwd(repo)) == "main"

    ctx = resolve_stack_context(repo, branch="feature", settings=Settings.from_cwd(repo))
    assert ctx.push_branch == "main"
    assert ctx.target_branch == "main"


def test_resolve_stack_context_errors_when_project_missing(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git("init", "-b", "main", cwd=repo)
    (repo / "f").write_text("x", encoding="utf-8")
    git("add", "f", cwd=repo)
    git("commit", "-m", "init", cwd=repo)
    git("checkout", "-b", "feature", cwd=repo)
    configure_gerrit_target(repo, "main")

    with pytest.raises(ChangeResolutionError, match="gerrit.project"):
        resolve_stack_context(repo, branch="feature", settings=Settings.from_cwd(repo))


def test_resolve_stack_context_errors_when_destination_missing(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git("init", "-b", "main", cwd=repo)
    (repo / "f").write_text("x", encoding="utf-8")
    git("add", "f", cwd=repo)
    git("commit", "-m", "init", cwd=repo)
    git("remote", "add", "origin", "ssh://user@gerrit.example.com/a/proj.git", cwd=repo)
    git("checkout", "-b", "feature", cwd=repo)

    with pytest.raises(ChangeResolutionError, match="gerritTarget"):
        resolve_stack_context(repo, branch="feature", settings=Settings.from_cwd(repo))


def test_narrowing_target_branch_selected(stack_repo: Path) -> None:
    _configure_stack_repo_project(stack_repo)
    rows = [
        _change_row(number=120045, branch="main"),
        _change_row(number=119870, branch="release-2.1"),
    ]
    client = _mock_client({f"change:{CHANGE_ID}": rows})

    resolution = resolve_changeish(CHANGE_ID, client=client, cwd=stack_repo, settings=Settings.from_cwd(stack_repo))

    assert resolution.selected is not None
    assert resolution.selected.number == 120045
    assert resolution.selected_reason == "target-branch"
    assert resolution.ambiguous is True
    assert len(resolution.alternatives) == 1
    assert resolution.alternatives[0].number == 119870


def test_narrowing_prefer_open_on_target_branch(stack_repo: Path) -> None:
    _configure_stack_repo_project(stack_repo)
    rows = [
        _change_row(number=120045, branch="main", status="NEW"),
        _change_row(number=119999, branch="main", status="ABANDONED"),
    ]
    client = _mock_client({f"change:{CHANGE_ID}": rows})

    resolution = resolve_changeish(CHANGE_ID, client=client, cwd=stack_repo, settings=Settings.from_cwd(stack_repo))

    assert resolution.selected is not None
    assert resolution.selected.number == 120045
    assert resolution.selected_reason == "prefer-open"
    assert resolution.ambiguous is True
    assert resolution.alternatives[0].number == 119999


def test_narrowing_two_open_on_target_raises(stack_repo: Path) -> None:
    _configure_stack_repo_project(stack_repo)
    rows = [
        _change_row(number=120045, branch="main", status="NEW"),
        _change_row(number=120046, branch="main", status="DRAFT"),
    ]
    client = _mock_client({f"change:{CHANGE_ID}": rows})

    with pytest.raises(ChangeAmbiguousError) as exc:
        resolve_changeish(CHANGE_ID, client=client, cwd=stack_repo, settings=Settings.from_cwd(stack_repo))

    assert len(exc.value.alternatives) == 2
    nums = {alt.number for alt in exc.value.alternatives}
    assert nums == {120045, 120046}


def test_change_id_only_on_other_branch_is_absent(stack_repo: Path) -> None:
    _configure_stack_repo_project(stack_repo)
    rows = [_change_row(number=119870, branch="release-2.1")]
    client = _mock_client({f"change:{CHANGE_ID}": rows})

    resolution = resolve_changeish(
        CHANGE_ID, client=client, cwd=stack_repo, settings=Settings.from_cwd(stack_repo), explicit_target=False
    )

    assert resolution.selected is None
    assert len(resolution.alternatives) == 1
    note = format_resolution_note(resolution)
    assert note is not None
    assert "absent on your push target" in note


def test_change_id_case_sensitive_queries(stack_repo: Path) -> None:
    _configure_stack_repo_project(stack_repo)
    client = MagicMock()

    def query_changes(query: str, *, n: int = 25, options: list[str] | None = None) -> list[dict[str, Any]]:
        del n, options
        if query == f"change:{CHANGE_ID}":
            return [_change_row(number=1, branch="main", change_id=CHANGE_ID)]
        if query == f"change:{CHANGE_ID_OTHER_CASE}":
            return [_change_row(number=2, branch="main", change_id=CHANGE_ID_OTHER_CASE)]
        return []

    client.query_changes.side_effect = query_changes

    first = resolve_changeish(CHANGE_ID, client=client, cwd=stack_repo, settings=Settings.from_cwd(stack_repo))
    second = resolve_changeish(
        CHANGE_ID_OTHER_CASE, client=client, cwd=stack_repo, settings=Settings.from_cwd(stack_repo)
    )

    assert first.selected is not None
    assert second.selected is not None
    assert first.selected.number == 1
    assert second.selected.number == 2
    assert client.query_changes.call_args_list[0].args[0] != client.query_changes.call_args_list[1].args[0]


def test_resolve_triplet(stack_repo: Path) -> None:
    _configure_stack_repo_project(stack_repo)
    triplet = build_triplet("myproject", "main", CHANGE_ID)
    row = _change_row(number=120045, branch="main")
    client = _mock_client({"": [row]})

    resolution = resolve_changeish(triplet, client=client, cwd=stack_repo, settings=Settings.from_cwd(stack_repo))

    assert resolution.kind == "triplet"
    assert resolution.selected is not None
    assert resolution.selected.triplet == row["id"]
    assert resolution.selected_reason == "unique"


def test_resolve_change_number(stack_repo: Path) -> None:
    _configure_stack_repo_project(stack_repo)
    row = _change_row(number=120045, branch="main")
    client = _mock_client({"": [row]})

    resolution = resolve_changeish(
        "change:120045", client=client, cwd=stack_repo, settings=Settings.from_cwd(stack_repo)
    )

    assert resolution.kind == "change-number"
    assert resolution.selected is not None
    assert resolution.selected.number == 120045


def test_git_rev_resolves_without_gerrit_query(stack_repo: Path) -> None:
    _configure_stack_repo_project(stack_repo)
    client = MagicMock()
    # Use init commit (no Change-Id footer) so git-rev path does not query Gerrit.
    sha = git_out("rev-parse", "main", cwd=stack_repo)

    resolution = resolve_changeish("main", client=client, cwd=stack_repo, settings=Settings.from_cwd(stack_repo))

    assert resolution.kind == "git-rev"
    assert resolution.local_sha == sha
    client.query_changes.assert_not_called()
