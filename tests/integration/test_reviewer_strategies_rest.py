"""Integration: lazy / overwrite reviewer strategies apply via Gerrit REST after push."""

from __future__ import annotations

import secrets

import pytest

from gerrit_workflow_tools.cli_push import main as ger_push_main
from gerrit_workflow_tools.core.git_run import git
from tests.conftest import run_cli
from tests.integration.gerrit_http import GerritHttpSession, quote_change_id
from tests.integration.gerrit_seed import create_account
from tests.integration.integration_helpers import (
    first_change_id_from_tip,
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
    rev_alt = f"rev_{secrets.token_hex(4)}"
    create_account(
        gerrit_admin_session,
        rev_alt,
        email=f"{rev_alt}@example.com",
        http_password=f"pw-{secrets.token_hex(8)}",
    )
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
    rev_alt = f"rvo_{secrets.token_hex(4)}"
    create_account(
        gerrit_admin_session,
        rev_alt,
        email=f"{rev_alt}@example.com",
        http_password=f"pw-{secrets.token_hex(8)}",
    )
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
