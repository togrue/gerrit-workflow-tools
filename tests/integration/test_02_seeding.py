"""Checkpoint 2: seeded projects and branches exist."""

from __future__ import annotations

from tests.integration.gerrit_seed import list_branches


def test_seeded_branches_exist(gerrit_admin_session, gerrit_integration_context) -> None:
    for proj in (gerrit_integration_context.project_verified, gerrit_integration_context.project_plain):
        branches = list_branches(gerrit_admin_session, proj)
        ref_names = {str(b.get("ref", "")) for b in branches}
        assert "refs/heads/main" in ref_names or "refs/heads/master" in ref_names
        assert "refs/heads/dev" in ref_names
        assert "refs/heads/hotfix_123" in ref_names
