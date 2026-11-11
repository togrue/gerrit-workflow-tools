"""Integration: what ``ger log``, ``ger show`` and ``ger push`` say about a change's state on Gerrit.

WIP and ``--ready``, private changes with topic and reviewers, a patchset published in Gerrit's
web editor, a change Gerrit rebased, an abandoned change, and an upload Gerrit's permissions
refuse. Each of these is something only the real server decides: the in-memory ``ChangeStore``
answers whatever a test told it to.
"""

from __future__ import annotations

import secrets
from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_log import main as ger_log_main
from gerrit_workflow_tools.cli_push import main as ger_push_main
from gerrit_workflow_tools.cli_show import main as ger_show_main
from gerrit_workflow_tools.core.git_run import git, git_out
from tests.helpers import force_zero_change_trust_window
from tests.integration.conftest import GerritIntegrationContext
from tests.integration.gerrit_http import GerritHttpSession
from tests.integration.gerrit_seed import block_review_uploads
from tests.integration.integration_helpers import (
    abandon_change,
    chain_commits_oldest_first,
    change_detail,
    current_patchset,
    ger_json,
    ger_push,
    log_commits_by_change_id,
    prepare_topic_repo,
    publish_web_edit,
    rebase_change_on_gerrit,
    reviewer_slugs_from_reviewers_rest,
    run_ger,
)
from tests.integration.repo_builder import build_linear_chain


def _pushed_stack(
    ctx: GerritIntegrationContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, *push_args: str
) -> tuple[Path, str, list[str]]:
    """A topic branch with a two-commit stack pushed by ``ger push``; ``(repo, branch, triplets)``, base first."""
    branch = f"{name}_{secrets.token_hex(4)}"
    repo = prepare_topic_repo(ctx, tmp_path, branch)
    build_linear_chain(repo, [f"{name} base", f"{name} top"])
    ger_push(repo, monkeypatch, *push_args)
    return repo, branch, [f"{ctx.project_verified}~{branch}~{c.change_id}" for c in chain_commits_oldest_first(repo)]


def _amend_top(repo: Path, content: str) -> None:
    (repo / "chain_1.txt").write_text(content, encoding="utf-8")
    git("commit", "-q", "-a", "--amend", "--no-edit", cwd=repo)


def test_wip_changes_become_ready_with_the_patchset_that_carries_ready(
    tmp_path: Path,
    gerrit_integration_context: GerritIntegrationContext,
    gerrit_admin_session: GerritHttpSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _branch, (base, top) = _pushed_stack(gerrit_integration_context, tmp_path, monkeypatch, "draft", "--wip")
    code, out, _err = run_ger(repo, ger_log_main, ["--color", "never"], monkeypatch)
    assert code == 1
    assert "WIP 2" in out and "ger push --ready" in out

    _amend_top(repo, "reworked\n")
    _out, err = ger_push(repo, monkeypatch, "--ready")
    # Gerrit applies %ready only with a new patchset, and ger says which commit it missed.
    assert f"ready not applied to unchanged commits: {git_out('rev-parse', '--short', 'HEAD~1', cwd=repo)}" in err
    assert change_detail(gerrit_admin_session, top).get("work_in_progress") is not True
    assert change_detail(gerrit_admin_session, base).get("work_in_progress") is True
    force_zero_change_trust_window(monkeypatch)
    rows = log_commits_by_change_id(repo, monkeypatch)
    assert [row["work_in_progress"] for row in rows.values()] == [True, False]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "product gap: ger log advises `ger push --ready` for WIP changes, but on an unchanged stack "
        "Gerrit gets no new patchset, so %ready is dropped and the changes stay WIP"
    ),
)
def test_push_ready_marks_an_unchanged_wip_stack_ready(
    tmp_path: Path,
    gerrit_integration_context: GerritIntegrationContext,
    gerrit_admin_session: GerritHttpSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _branch, triplets = _pushed_stack(gerrit_integration_context, tmp_path, monkeypatch, "ready", "--wip")
    ger_push(repo, monkeypatch, "--ready")
    assert [change_detail(gerrit_admin_session, t).get("work_in_progress") for t in triplets] == [None, None]


def test_private_push_with_topic_and_reviewers_lands_on_gerrit_and_in_log(
    tmp_path: Path,
    gerrit_integration_context: GerritIntegrationContext,
    gerrit_admin_session: GerritHttpSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = gerrit_integration_context
    topic = f"tp_{secrets.token_hex(3)}"
    args = ("--private", "--topic", topic, "--reviewers", ctx.rev_alt_user)
    repo, _branch, triplets = _pushed_stack(ctx, tmp_path, monkeypatch, "private", *args)
    for triplet in triplets:
        detail = change_detail(gerrit_admin_session, triplet)
        assert (detail.get("is_private"), detail.get("topic")) == (True, topic)
        assert ctx.rev_alt_user in reviewer_slugs_from_reviewers_rest(gerrit_admin_session, triplet)
    assert all(row["private"] for row in log_commits_by_change_id(repo, monkeypatch).values())


def test_a_web_edit_and_a_gerrit_rebase_show_as_outdated_and_push_keeps_them(
    tmp_path: Path,
    gerrit_integration_context: GerritIntegrationContext,
    gerrit_admin_session: GerritHttpSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Patchsets made on Gerrit are not in the clone: log and show say so; a push does not undo them."""
    repo, _branch, (base, top) = _pushed_stack(gerrit_integration_context, tmp_path, monkeypatch, "webedit")
    force_zero_change_trust_window(monkeypatch)
    publish_web_edit(gerrit_admin_session, base, "chain_0.txt", "edited in the browser\n")
    rows = list(log_commits_by_change_id(repo, monkeypatch).values())
    assert [row["patchset_status"] for row in rows] == ["outdated", "active"]
    assert "outdated-patchset" in rows[0]["attention_reasons"]

    # Rebase moves the top change onto the edited base: now both local commits are behind.
    rebase_change_on_gerrit(gerrit_admin_session, top)
    shown = ger_json(repo, ger_show_main, ["HEAD"], monkeypatch)
    assert shown["patchset_status"] == "outdated"
    assert [row["patchset_status"] for row in log_commits_by_change_id(repo, monkeypatch).values()] == [
        "outdated",
        "outdated",
    ]

    # The local commits are patchset 1 of each: Gerrit has them, so nothing is uploaded.
    out, _err = ger_push(repo, monkeypatch)
    assert "nothing to push: Gerrit already has these commits" in out
    assert [current_patchset(gerrit_admin_session, t) for t in (base, top)] == [2, 2]


def test_a_push_gerrit_rejects_exits_nonzero_with_gerrits_reason(
    tmp_path: Path,
    gerrit_integration_context: GerritIntegrationContext,
    gerrit_admin_session: GerritHttpSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = gerrit_integration_context
    repo, branch, (_base, top) = _pushed_stack(ctx, tmp_path, monkeypatch, "rejected")
    number = change_detail(gerrit_admin_session, top)["_number"]

    # A new patchset for an abandoned change: ger warns before pushing, Gerrit refuses.
    abandon_change(gerrit_admin_session, top)
    _amend_top(repo, "after abandon\n")
    code, _out, err = run_ger(repo, ger_push_main, ["--yes", "--no-rebase-check"], monkeypatch)
    assert code != 0
    assert f"targets abandoned change {number}" in err
    assert f"/+/{number} closed" in err and "[remote rejected]" in err

    # No permission to upload for review: Gerrit's own words reach the user.
    block_review_uploads(gerrit_admin_session, ctx.project_verified, branch)
    git("reset", "-q", "--hard", "HEAD~1", cwd=repo)
    build_linear_chain(repo, ["rejected new change"])
    code, _out, err = run_ger(repo, ger_push_main, ["--yes", "--no-rebase-check"], monkeypatch)
    assert code != 0
    assert "[remote rejected]" in err and "prohibited" in err.lower(), err
