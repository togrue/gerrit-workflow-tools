"""Integration: lazy / overwrite reviewer strategies apply via Gerrit REST after push."""

from __future__ import annotations

import secrets

import pytest

from gerrit_workflow_tools.cli_push import main as ger_push_main
from gerrit_workflow_tools.core.git_run import git
from tests.conftest import run_cli
from tests.integration.gerrit_http import GerritHttpSession, quote_change_id
from tests.integration.integration_helpers import (
    first_change_id_from_tip,
    open_changes_on_branch,
    prepare_topic_repo,
    reviewer_slugs_from_reviewers_rest,
    reviewer_slugs_on_change,
)
from tests.integration.repo_builder import build_linear_chain


def _reviewer_slugs_re_fetched_from_gerrit(
    session: GerritHttpSession,
    project: str,
    topic: str,
) -> set[str]:
    """
    Re-query Gerrit after the push: compare change ``detail`` reviewers with
    ``GET changes/<id>/reviewers/`` so we assert on live server state, not CLI output.
    """

    cid = first_change_id_from_tip(session, project, topic)
    assert cid, "expected an open change on topic branch"
    enc = quote_change_id(cid)
    detail = session.get_json(f"changes/{enc}/detail")
    assert isinstance(detail, dict)
    from_detail = set(reviewer_slugs_on_change(detail))
    from_rest = set(reviewer_slugs_from_reviewers_rest(session, cid))
    assert from_detail == from_rest, f"detail.reviewers and GET /reviewers/ disagree: {from_detail!r} vs {from_rest!r}"
    return from_rest


def _revision_count_from_detail(detail: dict[str, object]) -> int:
    revisions = detail.get("revisions")
    if not isinstance(revisions, dict):
        return 0
    return len(revisions)


def _snapshot_revision_counts(
    session: GerritHttpSession,
    project: str,
    topic: str,
) -> dict[str, int]:
    rows = open_changes_on_branch(session, project, topic)
    out: dict[str, int] = {}
    for row in rows:
        cid = row.get("change_id") or row.get("id")
        assert cid, f"missing change id in row: {row!r}"
        cid_str = str(cid)
        enc = quote_change_id(cid_str)
        detail = session.get_json(f"changes/{enc}/detail")
        assert isinstance(detail, dict)
        out[cid_str] = _revision_count_from_detail(detail)
    return out


def _assert_all_open_changes_reviewers(
    session: GerritHttpSession,
    project: str,
    topic: str,
    *,
    must_include: set[str],
    must_exclude: set[str] | None = None,
    expected_change_count: int | None = None,
) -> None:
    rows = open_changes_on_branch(session, project, topic)
    if expected_change_count is not None:
        assert len(rows) == expected_change_count, rows
    assert rows, "expected open changes on topic branch"

    for row in rows:
        cid = row.get("change_id") or row.get("id")
        assert cid, f"missing change id in row: {row!r}"
        cid_str = str(cid)
        enc = quote_change_id(cid_str)
        detail = session.get_json(f"changes/{enc}/detail")
        assert isinstance(detail, dict)
        from_detail = set(reviewer_slugs_on_change(detail))
        from_rest = set(reviewer_slugs_from_reviewers_rest(session, cid_str))
        assert from_detail == from_rest, (
            f"detail.reviewers and GET /reviewers/ disagree for {cid_str}: {from_detail!r} vs {from_rest!r}"
        )
        for reviewer in must_include:
            assert reviewer in from_rest, f"{reviewer!r} missing on change {cid_str}; got {sorted(from_rest)!r}"
        for reviewer in must_exclude or set():
            assert reviewer not in from_rest, f"{reviewer!r} unexpectedly present on change {cid_str}"


def test_lazy_reviewer_strategy_adds_via_rest(
    tmp_path,
    gerrit_integration_context,
    gerrit_admin_session: GerritHttpSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = gerrit_integration_context
    topic = f"lz_{secrets.token_hex(4)}"
    repo = prepare_topic_repo(ctx, tmp_path, topic)
    build_linear_chain(repo, ["feat: lazy adds reviewers"])
    code, _out, err = run_cli(
        repo,
        ger_push_main,
        [
            "--yes",
            "--no-rebase-check",
            "--reviewer-strategy",
            "lazy",
            "--reviewers",
            ctx.admin_user,
        ],
        monkeypatch,
    )
    assert code == 0, err
    slugs = _reviewer_slugs_re_fetched_from_gerrit(gerrit_admin_session, ctx.project_verified, topic)
    assert ctx.admin_user in slugs


def test_lazy_reviewer_strategy_skips_when_reviewers_exist(
    tmp_path,
    gerrit_integration_context,
    gerrit_admin_session: GerritHttpSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = gerrit_integration_context
    rev_alt = ctx.rev_alt_user
    topic = f"lz2_{secrets.token_hex(4)}"
    repo = prepare_topic_repo(ctx, tmp_path, topic)
    build_linear_chain(repo, ["feat: lazy skip"])
    code1, _o1, e1 = run_cli(
        repo,
        ger_push_main,
        [
            "--yes",
            "--no-rebase-check",
            "--reviewer-strategy",
            "lazy",
            "--reviewers",
            ctx.admin_user,
        ],
        monkeypatch,
    )
    assert code1 == 0, e1
    slugs1 = _reviewer_slugs_re_fetched_from_gerrit(gerrit_admin_session, ctx.project_verified, topic)
    assert ctx.admin_user in slugs1

    git("commit", "--amend", "--no-edit", cwd=repo)
    code2, _o2, e2 = run_cli(
        repo,
        ger_push_main,
        [
            "--yes",
            "--no-rebase-check",
            "--reviewer-strategy",
            "lazy",
            "--reviewers",
            rev_alt,
        ],
        monkeypatch,
    )
    assert code2 == 0, e2
    slugs2 = _reviewer_slugs_re_fetched_from_gerrit(gerrit_admin_session, ctx.project_verified, topic)
    assert ctx.admin_user in slugs2
    assert rev_alt not in slugs2


def test_overwrite_reviewer_strategy_replaces_reviewers(
    tmp_path,
    gerrit_integration_context,
    gerrit_admin_session: GerritHttpSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = gerrit_integration_context
    rev_alt = ctx.rev_alt_user
    topic = f"ow_{secrets.token_hex(4)}"
    repo = prepare_topic_repo(ctx, tmp_path, topic)
    build_linear_chain(repo, ["feat: overwrite reviewers"])
    code1, _o1, e1 = run_cli(
        repo,
        ger_push_main,
        [
            "--yes",
            "--no-rebase-check",
            "--reviewer-strategy",
            "lazy",
            "--reviewers",
            ctx.admin_user,
        ],
        monkeypatch,
    )
    assert code1 == 0, e1
    slugs1b = _reviewer_slugs_re_fetched_from_gerrit(gerrit_admin_session, ctx.project_verified, topic)
    assert ctx.admin_user in slugs1b

    git("commit", "--amend", "--no-edit", cwd=repo)
    code2, _o2, e2 = run_cli(
        repo,
        ger_push_main,
        [
            "--yes",
            "--no-rebase-check",
            "--reviewer-strategy",
            "overwrite",
            "--reviewers",
            rev_alt,
        ],
        monkeypatch,
    )
    assert code2 == 0, e2
    slugs2b = _reviewer_slugs_re_fetched_from_gerrit(gerrit_admin_session, ctx.project_verified, topic)
    assert rev_alt in slugs2b
    assert ctx.admin_user not in slugs2b


def test_reviewer_strategy_assigns_after_initial_push_without_reviewers(
    tmp_path,
    gerrit_integration_context,
    gerrit_admin_session: GerritHttpSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lazy and overwrite both assign reviewers on a second push without new patch sets."""
    ctx = gerrit_integration_context
    cases: tuple[tuple[str, str, set[str] | None], ...] = (
        ("lazy", ctx.admin_user, None),
        ("overwrite", ctx.rev_alt_user, {ctx.admin_user}),
    )
    for strategy, rev, exclude in cases:
        topic = f"2ph_{strategy}_{secrets.token_hex(4)}"
        repo = prepare_topic_repo(ctx, tmp_path, topic)
        build_linear_chain(
            repo,
            [
                f"feat: {strategy} two-phase first",
                f"feat: {strategy} two-phase second",
            ],
        )

        code1, _o1, e1 = run_cli(
            repo,
            ger_push_main,
            ["--yes", "--no-rebase-check"],
            monkeypatch,
        )
        assert code1 == 0, e1
        before_revisions = _snapshot_revision_counts(gerrit_admin_session, ctx.project_verified, topic)
        assert len(before_revisions) == 2

        code2, _o2, e2 = run_cli(
            repo,
            ger_push_main,
            [
                "--yes",
                "--no-rebase-check",
                "--reviewer-strategy",
                strategy,
                "--reviewers",
                rev,
            ],
            monkeypatch,
        )
        assert code2 == 0, e2
        _assert_all_open_changes_reviewers(
            gerrit_admin_session,
            ctx.project_verified,
            topic,
            must_include={rev},
            must_exclude=exclude,
            expected_change_count=2,
        )
        after_revisions = _snapshot_revision_counts(gerrit_admin_session, ctx.project_verified, topic)
        assert after_revisions == before_revisions
