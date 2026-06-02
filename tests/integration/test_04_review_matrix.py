"""Checkpoint 6: Code-Review and Verified vote combinations via REST (after a real push)."""

from __future__ import annotations

import secrets
from itertools import product

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

# All 15 combinations in one push (was 15 parametrized tests x full prepare+push each).
VOTE_CASES: tuple[tuple[int, int], ...] = tuple(product(CRS, VS))


def _row_for_vote_case(rows: list[dict[str, object]], cr: int, v: int) -> dict[str, object]:
    needle = f"cr={cr} v={v}"
    for row in rows:
        subject = str(row.get("subject") or "")
        if needle in subject:
            return row
    raise AssertionError(f"no change with subject containing {needle!r}; got {[r.get('subject') for r in rows]}")


def test_review_vote_matrix(
    tmp_path,
    gerrit_integration_context,
    gerrit_admin_session: GerritHttpSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vote every CR x Verified pair on one stacked push (one topic repo, one ``ger push --all``)."""
    topic = f"rv_{secrets.token_hex(6)}"
    repo = prepare_topic_repo(gerrit_integration_context, tmp_path, topic)
    build_linear_chain(
        repo,
        [f"matrix commit cr={cr} v={v}" for cr, v in VOTE_CASES],
    )
    code, _out, err = run_cli(
        repo,
        ger_push_main,
        ["--yes", "--all", "--no-rebase-check"],
        monkeypatch,
    )
    assert code == 0, err

    proj = gerrit_integration_context.project_verified
    rows = open_changes_on_branch(gerrit_admin_session, proj, topic)
    assert len(rows) == len(VOTE_CASES), (
        f"expected {len(VOTE_CASES)} open changes, got {len(rows)}: {[r.get('subject') for r in rows]}"
    )

    for cr, v in VOTE_CASES:
        row = _row_for_vote_case(rows, cr, v)
        cid = str(row.get("change_id") or row.get("id"))
        post_review_labels(gerrit_admin_session, cid, code_review=cr, verified=v)

        enc = quote_change_id(cid)
        detail = gerrit_admin_session.get_json(
            f"changes/{enc}/detail",
            params=[("o", "SUBMITTABLE")],
        )
        assert label_value(detail, "Code-Review") == cr, (cr, v, detail.get("labels"))
        assert label_value(detail, "Verified") == v, (cr, v, detail.get("labels"))
        if cr == 2 and v == 1:
            assert detail.get("submittable") is True, (cr, v, detail)
