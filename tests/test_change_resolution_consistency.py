"""Phase 6: cross-command resolution consistency (unit tests).

Default CI runs these without Docker. End-to-end coverage against a live Gerrit
instance lives in ``tests/integration/test_09_change_resolution.py``.

See ``docu/plans/gerrit-native-change-resolution.md`` Phase 6 for the full matrix.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_log import main as log_main
from gerrit_workflow_tools.cli_resolve import main as resolve_main
from gerrit_workflow_tools.cli_show import main as gshow_main
from gerrit_workflow_tools.core.config import clear_gerrit_git_config_cache
from gerrit_workflow_tools.core.gerrit.change_resolution import build_triplet
from gerrit_workflow_tools.core.git_run import git, git_out
from tests.change_store import ChangeStore
from tests.cli_gerrit_mocks import (
    change_info_for_sha,
    stack_rows_mb_to_head,
)
from tests.conftest import json_stdout, run_cli
from tests.fixtures import _cid


def _configure_web(repo: Path) -> None:
    git("config", "gerrit.webUrl", "https://g.example", cwd=repo)
    clear_gerrit_git_config_cache()


def _mock_dual_branch_details(
    *,
    change_id: str,
    sha: str,
    main_number: int = 120100,
    dev_number: int = 119900,
    main_cr: int = 2,
    dev_cr: int = -1,
) -> dict[str, dict]:
    main_row = change_info_for_sha(
        sha,
        change_id,
        number=main_number,
        branch="main",
        cr=main_cr,
    )
    dev_row = change_info_for_sha(
        sha,
        change_id,
        number=dev_number,
        branch="dev",
        cr=dev_cr,
    )
    return {str(main_row["id"]): main_row, str(dev_row["id"]): dev_row}


def _configure_dev_gerrit_target(repo: Path) -> None:
    """Point stack context at Gerrit destination branch ``dev``."""
    git("config", "branch.feature.gerritTarget", "dev", cwd=repo)
    main_sha = git_out("rev-parse", "main", cwd=repo).strip()
    git("update-ref", "refs/remotes/origin/dev", main_sha, cwd=repo)
    clear_gerrit_git_config_cache()


def test_resolve_and_show_json_agree_on_narrowed_change_id(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``ger resolve --json`` and ``ger show --json`` emit the same ``resolution`` block."""
    _configure_web(stack_repo)
    cid = _cid("a6")
    sha = git_out("rev-parse", "HEAD~1", cwd=stack_repo)
    details = _mock_dual_branch_details(change_id=cid, sha=sha)
    # One store for both commands: they must agree against identical Gerrit state.
    store = ChangeStore(details)
    code_r, out_r, err_r = run_cli(stack_repo, resolve_main, ["--json", cid], monkeypatch, gerrit=store)
    code_s, out_s, err_s = run_cli(stack_repo, gshow_main, ["--json", cid], monkeypatch, gerrit=store)
    assert code_r == 0, err_r
    assert code_s == 0, err_s
    resolve_block = json_stdout(out_r)["resolution"]
    show_block = json_stdout(out_s)["resolution"]
    assert resolve_block == show_block
    assert resolve_block["selected_reason"] == "target-branch"
    assert resolve_block["selected"]["branch"] == "main"
    assert resolve_block["selected"]["number"] == 120100
    assert "119900" in err_r
    assert "119900" in err_s


def test_log_on_main_target_uses_main_triplet_not_dev(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``ger log --json`` on a main-target stack overlays the main-branch Gerrit change."""
    _configure_web(stack_repo)
    sha = git_out("rev-parse", "HEAD~2", cwd=stack_repo)
    cid = _cid("2")
    details = _mock_dual_branch_details(change_id=cid, sha=sha, main_cr=2, dev_cr=-1)
    code, out, err = run_cli(stack_repo, log_main, ["--json"], monkeypatch, gerrit=ChangeStore(details))
    assert code in (0, 1), err
    commits = json_stdout(out)["commits"]
    row = next(c for c in commits if c["change_id"] == cid)
    assert row["pushed"] is True
    assert row["code_review"] == 2
    assert "/+/120100" in (row.get("gerrit_url") or "")
    assert "resolution_note" in row
    assert "120100" in row["resolution_note"]
    assert "119900" in row["resolution_note"]


def test_log_resolution_notes_do_not_requery_per_change_id(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Notes must come from cache/batch payloads — no bare change:I storm after overlay."""
    _configure_web(stack_repo)
    sha = git_out("rev-parse", "HEAD~2", cwd=stack_repo)
    cid = _cid("2")
    details = _mock_dual_branch_details(change_id=cid, sha=sha)
    store = ChangeStore(details)
    code, out, err = run_cli(stack_repo, log_main, ["--json"], monkeypatch, gerrit=store)
    assert code in (0, 1), err
    bare = [query for query in store.queries() if query.startswith("change:")]
    assert bare == [], f"unexpected per-Change-Id queries: {bare!r}"
    row = next(c for c in json_stdout(out)["commits"] if c["change_id"] == cid)
    assert "resolution_note" in row


def test_log_on_dev_target_uses_dev_triplet_not_main(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``ger log --json`` with ``branch.*.gerritTarget=dev`` overlays the dev-branch change."""
    _configure_web(stack_repo)
    _configure_dev_gerrit_target(stack_repo)
    sha = git_out("rev-parse", "HEAD~2", cwd=stack_repo)
    cid = _cid("2")
    details = _mock_dual_branch_details(change_id=cid, sha=sha, main_cr=2, dev_cr=-1)
    code, out, err = run_cli(stack_repo, log_main, ["--json"], monkeypatch, gerrit=ChangeStore(details))
    assert code in (0, 1), err
    commits = json_stdout(out)["commits"]
    row = next(c for c in commits if c["change_id"] == cid)
    assert row["pushed"] is True
    assert row["code_review"] == -1
    assert "/+/119900" in (row.get("gerrit_url") or "")


def test_log_absent_when_change_only_on_other_branch(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Target-branch overlay must not pick a Change-Id that exists only on another branch."""
    _configure_web(stack_repo)
    _configure_dev_gerrit_target(stack_repo)
    sha = git_out("rev-parse", "HEAD~2", cwd=stack_repo)
    cid = _cid("2")
    # Only main-branch row exists; stack target is dev → absent.
    main_only = {
        str(change_info_for_sha(sha, cid, number=120100, branch="main", cr=2)["id"]): change_info_for_sha(
            sha, cid, number=120100, branch="main", cr=2
        )
    }
    code, out, err = run_cli(stack_repo, log_main, ["--json"], monkeypatch, gerrit=ChangeStore(main_only))
    assert code in (0, 1), err
    commits = json_stdout(out)["commits"]
    row = next(c for c in commits if c["change_id"] == cid)
    assert row["pushed"] is False
    assert "/+/120100" not in (row.get("gerrit_url") or "")


def test_push_stack_resolve_and_show_agree_on_triplet(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """After stack overlay, ``ger resolve`` and ``ger show`` pick the same triplet as ``ger log``."""
    from tests.cli_gerrit_mocks import build_details_by_change_id

    _configure_web(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    cid = rows[0].change_id
    assert cid is not None
    expected_triplet = build_triplet("testproj", "main", cid)

    # One store for all three commands: they must pick the same triplet from one state.
    store = ChangeStore(details)
    code_r, out_r, err_r = run_cli(stack_repo, resolve_main, ["--json", cid], monkeypatch, gerrit=store)
    code_s, out_s, err_s = run_cli(stack_repo, gshow_main, ["--json", cid], monkeypatch, gerrit=store)
    code_l, out_l, err_l = run_cli(stack_repo, log_main, ["--json"], monkeypatch, gerrit=store)
    assert code_r == 0, err_r
    assert code_s == 0, err_s
    assert code_l == 0, err_l

    resolve_triplet = json_stdout(out_r)["resolution"]["selected"]["triplet"]
    show_triplet = json_stdout(out_s)["resolution"]["selected"]["triplet"]
    assert resolve_triplet == show_triplet == expected_triplet

    log_row = next(c for c in json_stdout(out_l)["commits"] if c["change_id"] == cid)
    assert log_row["pushed"] is True
    assert f"/+/{json_stdout(out_r)['resolution']['selected']['number']}" in (log_row.get("gerrit_url") or "")


def test_bare_integer_fix_documented_in_fix_tests(stack_repo: Path) -> None:
    """Bare-integer ``ger fix`` behavior is covered by ``tests/test_fix.py`` (spec §2.2)."""
    # Guardrail: keep Phase 6 matrix discoverable from this module.
    import tests.test_fix as fix_tests

    assert hasattr(fix_tests, "test_ger_fix_bare_integer_is_git_revision_not_change_number")
