"""Checkpoint 6: Code-Review and Verified vote combinations via REST (after a real push)."""

from __future__ import annotations

import secrets

import pytest

from gerrit_workflow_tools.cli_push import main as ger_push_main
from tests.conftest import run_cli
from tests.integration.gerrit_http import GerritHttpSession, quote_change_id
from tests.integration.gerrit_seed import post_review_labels
from tests.integration.integration_helpers import (
    label_value,
    open_changes_on_branch,
    prepare_topic_repo,
)
from tests.integration.repo_builder import build_linear_chain

CRS = (-2, -1, 0, 1, 2)
VS = (-1, 0, 1)


@pytest.mark.parametrize("cr", CRS)
@pytest.mark.parametrize("v", VS)
def test_review_vote_matrix(
    tmp_path,
    gerrit_integration_context,
    gerrit_admin_session: GerritHttpSession,
    monkeypatch: pytest.MonkeyPatch,
    cr: int,
    v: int,
) -> None:
    """Admin votes CR and Verified on a change uploaded by the dev user; assert label values."""
    topic = f"rv_{secrets.token_hex(6)}"
    repo = prepare_topic_repo(gerrit_integration_context, tmp_path, topic)
    build_linear_chain(repo, [f"matrix commit cr={cr} v={v}"])
    code, _out, err = run_cli(
        repo,
        ger_push_main,
        ["--yes", "--no-rebase-check"],
        monkeypatch,
    )
    assert code == 0, err

    proj = gerrit_integration_context.project_verified
    rows = open_changes_on_branch(gerrit_admin_session, proj, topic)
    assert len(rows) >= 1
    cid = str(rows[0].get("change_id") or rows[0].get("id"))
    post_review_labels(gerrit_admin_session, cid, code_review=cr, verified=v)

    enc = quote_change_id(cid)
    detail = gerrit_admin_session.get_json(
        f"changes/{enc}/detail",
        params=[("o", "SUBMITTABLE")],
    )
    assert label_value(detail, "Code-Review") == cr
    assert label_value(detail, "Verified") == v
    if cr == 2 and v == 1:
        assert detail.get("submittable") is True
