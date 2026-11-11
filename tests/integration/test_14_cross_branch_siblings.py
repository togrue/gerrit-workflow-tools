"""Integration: one Change-Id on two target branches, through every main command.

A commit pushed to ``main`` and cherry-picked to ``dev`` is two Gerrit changes with one
Change-Id. Gerrit answers a bare Change-Id query with both, so every command has to pick the
one its branch pushes to, and never show the sibling's number, patchset, votes or comments.
The in-memory ``ChangeStore`` cannot catch a leak here: it is only as cross-branch as the rows
a test hands it. ``ger`` runs as the dev user; votes and comments come from admin.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_fix import main as ger_fix_main
from gerrit_workflow_tools.cli_resolve import main as ger_resolve_main
from gerrit_workflow_tools.cli_show import main as ger_show_main
from gerrit_workflow_tools.core.git_run import git, git_out
from tests.helpers import force_zero_change_trust_window
from tests.integration.conftest import GerritIntegrationContext
from tests.integration.gerrit_http import GerritHttpSession
from tests.integration.gerrit_seed import post_review_labels
from tests.integration.integration_helpers import (
    SiblingChanges,
    abandon_change,
    current_patchset,
    ger_json,
    ger_push,
    log_commits_by_change_id,
    post_unresolved_inline_comment,
    push_sibling_changes,
    run_ger,
    submit_change,
)


@pytest.fixture(scope="module")
def reviewed_siblings(
    gerrit_integration_context: GerritIntegrationContext, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[SiblingChanges]:
    """Siblings with different review state: main's has Code-Review +1; dev's -1 and an open comment.

    Read-only for the tests that use it: each checks out the branch it needs.
    """
    ctx = gerrit_integration_context
    root = tmp_path_factory.mktemp("siblings")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("XDG_CACHE_HOME", str(root / "xdg-cache"))
        siblings = push_sibling_changes(ctx, root, mp, workdir="reviewed")
    admin = GerritHttpSession(ctx.http_base, user=ctx.admin_user, password=ctx.admin_password)
    post_review_labels(admin, siblings.triplet("main"), code_review=1)
    post_review_labels(admin, siblings.triplet("dev"), code_review=-1)
    post_unresolved_inline_comment(admin, siblings.triplet("dev"), "chain_0.txt", 1, "only on dev")
    # A branch that pushes to neither sibling's branch.
    git("checkout", "-q", "-b", "feat_hotfix", "origin/hotfix_123", cwd=siblings.repo)
    yield siblings


def _on(siblings: SiblingChanges, branch: str) -> Path:
    git("checkout", "-q", branch, cwd=siblings.repo)
    return siblings.repo


def test_log_on_each_branch_shows_its_own_change_and_its_own_review_state(
    reviewed_siblings: SiblingChanges, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = reviewed_siblings
    on_main = log_commits_by_change_id(_on(s, "feat_main"), monkeypatch)[s.change_id]
    assert (on_main["number"], on_main["code_review"], on_main["comments_unresolved"]) == (s.main["_number"], 1, 0)
    assert on_main["patchset_status"] == "active"
    # dev's comment and -1 are not main's attention, and ger show does not list the comment.
    assert not {"unresolved-comments", "review-issues"} & set(on_main["attention_reasons"])
    assert ger_json(s.repo, ger_show_main, ["HEAD"], monkeypatch)["comment_chains"] == []

    on_dev = log_commits_by_change_id(_on(s, "feat_dev"), monkeypatch)[s.change_id]
    assert (on_dev["number"], on_dev["code_review"], on_dev["comments_unresolved"]) == (s.dev["_number"], -1, 1)
    assert {"unresolved-comments", "review-issues"} <= set(on_dev["attention_reasons"])


def test_a_bare_change_id_is_narrowed_to_the_branch_or_refused_as_ambiguous(
    reviewed_siblings: SiblingChanges, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = reviewed_siblings
    repo = _on(s, "feat_dev")
    resolution = ger_json(repo, ger_resolve_main, [s.change_id], monkeypatch)["resolution"]
    assert resolution["selected"]["number"] == s.dev["_number"]
    assert resolution["selected_reason"] == "target-branch" and resolution["ambiguous"] is True
    assert [alt["number"] for alt in resolution["alternatives"]] == [s.main["_number"]]
    shown = ger_json(repo, ger_show_main, [s.change_id], monkeypatch)
    assert (shown["number"], shown["code_review"]) == (s.dev["_number"], -1)
    assert [chain["comments"][0]["message"] for chain in shown["comment_chains"]] == ["only on dev"]

    # From a branch that pushes to neither, nothing is guessed: both are named, exit 4.
    repo = _on(s, "feat_hotfix")
    for main_fn in (ger_resolve_main, ger_show_main):
        code, _out, err = run_ger(repo, main_fn, [s.change_id], monkeypatch)
        assert code == 4, err
        assert f"change:{s.main['_number']} (" in err and f"change:{s.dev['_number']} (" in err


@pytest.mark.parametrize("form", ["triplet", "change-number"])
def test_a_triplet_or_change_number_names_the_other_branchs_sibling_exactly(
    form: str, reviewed_siblings: SiblingChanges, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = reviewed_siblings
    changeish = s.triplet("dev") if form == "triplet" else f"change:{s.dev['_number']}"
    resolution = ger_json(_on(s, "feat_main"), ger_resolve_main, [changeish], monkeypatch)["resolution"]
    assert resolution["selected"]["number"] == s.dev["_number"]
    assert resolution["selected"]["branch"] == "dev"
    assert resolution["ambiguous"] is False


def test_show_of_the_other_branchs_sibling_reports_that_change(
    reviewed_siblings: SiblingChanges, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = reviewed_siblings
    shown = ger_json(_on(s, "feat_main"), ger_show_main, [s.triplet("dev")], monkeypatch)
    assert shown["resolution"]["selected"]["number"] == s.dev["_number"]
    assert (shown["number"], shown["code_review"]) == (s.dev["_number"], -1)
    # Not the local commit, which pushes to main: that one is named beside it.
    assert shown["local_commit"] is False and "local_sha" not in shown["resolution"]
    assert shown["resolution"]["local_sibling"]["sha"] == git_out("rev-parse", "HEAD", cwd=s.repo)
    assert [chain["comments"][0]["message"] for chain in shown["comment_chains"]] == ["only on dev"]
    assert all(f"/+/{s.dev['_number']}/" in chain["url"] for chain in shown["comment_chains"])


@pytest.mark.parametrize("form", ["change-id", "triplet", "change-number"])
def test_fix_targets_the_local_commit_of_this_branchs_change(
    form: str, reviewed_siblings: SiblingChanges, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = reviewed_siblings
    repo = _on(s, "feat_dev")
    target = git_out("rev-parse", "HEAD", cwd=repo)
    changeish = {
        "change-id": s.change_id,
        "triplet": s.triplet("dev"),
        "change-number": f"change:{s.dev['_number']}",
    }[form]
    (repo / "chain_0.txt").write_text("fixed on dev\n", encoding="utf-8")
    git("add", "chain_0.txt", cwd=repo)
    try:
        out = ger_json(repo, ger_fix_main, [changeish], monkeypatch)
        assert out["fixup_sha"] == target
        assert (
            git_out("log", "-1", "--format=%s", cwd=repo)
            == f"fixup! {git_out('log', '-1', '--format=%s', target, cwd=repo)}"
        )
    finally:
        git("reset", "-q", "--hard", target, cwd=repo)


def test_push_from_one_branch_renews_only_that_branchs_change(
    tmp_path: Path, gerrit_integration_context: GerritIntegrationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = gerrit_integration_context
    s = push_sibling_changes(ctx, tmp_path, monkeypatch)
    admin = GerritHttpSession(ctx.http_base, user=ctx.admin_user, password=ctx.admin_password)
    repo = _on(s, "feat_dev")
    (repo / "chain_0.txt").write_text("dev rework\n", encoding="utf-8")
    git("commit", "-q", "-a", "--amend", "--no-edit", cwd=repo)
    ger_push(repo, monkeypatch)
    assert (current_patchset(admin, s.triplet("dev")), current_patchset(admin, s.triplet("main"))) == (2, 1)

    # main's clone still holds main's current patchset: nothing to do there.
    force_zero_change_trust_window(monkeypatch)
    on_main = log_commits_by_change_id(_on(s, "feat_main"), monkeypatch)[s.change_id]
    assert (on_main["number"], on_main["patchset_status"]) == (s.main["_number"], "active")
    on_dev = log_commits_by_change_id(_on(s, "feat_dev"), monkeypatch)[s.change_id]
    assert (on_dev["number"], on_dev["patchset_status"]) == (s.dev["_number"], "active")


def test_an_abandoned_or_merged_sibling_leaves_the_open_one_as_it_is(
    tmp_path: Path, gerrit_integration_context: GerritIntegrationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = gerrit_integration_context
    s = push_sibling_changes(ctx, tmp_path, monkeypatch)
    admin = GerritHttpSession(ctx.http_base, user=ctx.admin_user, password=ctx.admin_password)
    force_zero_change_trust_window(monkeypatch)

    abandon_change(admin, s.triplet("dev"))
    on_main = log_commits_by_change_id(_on(s, "feat_main"), monkeypatch)[s.change_id]
    assert (on_main["change_status"], on_main["abandoned"]) == ("NEW", False)
    resolution = ger_json(s.repo, ger_resolve_main, [s.change_id], monkeypatch)["resolution"]
    assert (resolution["selected"]["number"], resolution["selected"]["status"]) == (s.main["_number"], "NEW")
    on_dev = log_commits_by_change_id(_on(s, "feat_dev"), monkeypatch)[s.change_id]
    assert (on_dev["number"], on_dev["abandoned"]) == (s.dev["_number"], True)

    submit_change(admin, s.triplet("main"))
    on_main = log_commits_by_change_id(_on(s, "feat_main"), monkeypatch)[s.change_id]
    assert (on_main["number"], on_main["change_status"]) == (s.main["_number"], "MERGED")
    on_dev = log_commits_by_change_id(_on(s, "feat_dev"), monkeypatch)[s.change_id]
    assert (on_dev["change_status"], on_dev["abandoned"]) == ("ABANDONED", True)
