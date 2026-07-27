"""Tests for core.annotated_stack — the shape log, show, edit and the enricher share."""

from __future__ import annotations

from pathlib import Path

import pytest

from gerrit_workflow_tools.core.annotated_stack import (
    annotate,
    commit_rows_in_range,
    load_annotated_stack,
    resolve_rev_range,
)
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.git_run import git, git_out
from tests.change_store import ChangeStore
from tests.cli_gerrit_mocks import build_details_by_change_id, change_info_for_sha, stack_rows_mb_to_head


def _configure(repo: Path) -> None:
    git("config", "gerrit.webUrl", "https://g.example", cwd=repo)


def _service(repo: Path, store: ChangeStore) -> GerritService:
    return GerritService.from_cwd(repo, settings=Settings.from_cwd(repo), rest=store)


# ---------------------------------------------------------------------------
# load_annotated_stack
# ---------------------------------------------------------------------------


def test_empty_range_returns_an_empty_stack_not_an_error(stack_repo: Path) -> None:
    """Whether "no commits" is a problem is the caller's call, so it is not an exception."""
    _configure(stack_repo)
    head = git_out("rev-parse", "HEAD", cwd=stack_repo)

    stack = load_annotated_stack(
        stack_repo, f"{head}..{head}", settings=Settings.from_cwd(stack_repo), gerrit=ChangeStore({})
    )

    assert stack.commits == []
    assert stack.notes_by_sha == {}
    assert not stack


def test_loads_commits_with_overlay_and_attention(stack_repo: Path) -> None:
    _configure(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    store = ChangeStore(build_details_by_change_id(rows), web_base="https://g.example")

    stack = load_annotated_stack(stack_repo, _range(stack_repo), settings=Settings.from_cwd(stack_repo), gerrit=store)

    assert [c.short_sha for c in stack.commits] == [r.short_sha for r in rows]
    assert all(c.pushed for c in stack.commits)
    # attention was annotated, not left unset
    assert all(isinstance(c.attention_reasons, list) for c in stack.commits)


def test_multi_branch_change_id_produces_a_resolution_note(stack_repo: Path) -> None:
    """The note comes from what the overlay already cached, keyed by commit SHA."""
    _configure(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    target = rows[0]
    assert target.change_id is not None
    payloads = build_details_by_change_id(rows)
    other_branch = change_info_for_sha(target.sha, target.change_id, branch="dev", number=999)
    payloads[str(other_branch["id"])] = other_branch

    stack = load_annotated_stack(
        stack_repo,
        _range(stack_repo),
        settings=Settings.from_cwd(stack_repo),
        gerrit=ChangeStore(payloads, web_base="https://g.example"),
    )

    assert target.sha in stack.notes_by_sha
    assert "999" in stack.notes_by_sha[target.sha]


def test_single_branch_change_ids_produce_no_notes(stack_repo: Path) -> None:
    _configure(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    store = ChangeStore(build_details_by_change_id(rows), web_base="https://g.example")

    stack = load_annotated_stack(stack_repo, _range(stack_repo), settings=Settings.from_cwd(stack_repo), gerrit=store)

    assert stack.notes_by_sha == {}


# ---------------------------------------------------------------------------
# annotate: the chain-blocking rule, which used to have two implementations
# ---------------------------------------------------------------------------


def test_an_unsubmittable_commit_blocks_the_ones_after_it(stack_repo: Path) -> None:
    _configure(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    assert len(rows) >= 3, "fixture must have enough commits to show blocking"
    overrides: list[dict] = [{} for _ in rows]
    overrides[0] = {"submittable": False, "cr": 0}
    store = ChangeStore(build_details_by_change_id(rows, per_index_overrides=overrides), web_base="https://g.example")

    commits = annotate(
        commit_rows_in_range(stack_repo, _range(stack_repo)), service=_service(stack_repo, store), cwd=stack_repo
    )

    assert "chain-blocked" not in commits[0].attention_reasons, "the blocker itself is not blocked"
    assert all("chain-blocked" in c.attention_reasons for c in commits[1:]), (
        "every commit after the blocker must be chain-blocked"
    )


def test_a_healthy_stack_blocks_nothing(stack_repo: Path) -> None:
    _configure(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    store = ChangeStore(build_details_by_change_id(rows), web_base="https://g.example")

    commits = annotate(
        commit_rows_in_range(stack_repo, _range(stack_repo)), service=_service(stack_repo, store), cwd=stack_repo
    )

    assert all("chain-blocked" not in c.attention_reasons for c in commits)


def test_a_lone_commit_is_never_chain_blocked(stack_repo: Path) -> None:
    """``ger show`` annotates one row; with no predecessors nothing can block it."""
    _configure(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    store = ChangeStore(
        build_details_by_change_id(rows, per_index_overrides=[{"submittable": False, "cr": 0}] * len(rows)),
        web_base="https://g.example",
    )
    single = commit_rows_in_range(stack_repo, _range(stack_repo))[-1:]

    commits = annotate(single, service=_service(stack_repo, store), cwd=stack_repo)

    assert len(commits) == 1
    assert "chain-blocked" not in commits[0].attention_reasons


def _range(repo: Path) -> str:
    return resolve_rev_range(repo, None, settings=Settings.from_cwd(repo))


@pytest.mark.parametrize(
    "arg, expected",
    [
        ("a..b", "a..b"),
        ("a...b", "a...b"),
        ("bak", "bak@{upstream}..bak"),
    ],
)
def test_resolve_rev_range_expands_bare_refs_only(stack_repo: Path, arg: str, expected: str) -> None:
    assert resolve_rev_range(stack_repo, arg, settings=Settings.from_cwd(stack_repo)) == expected
