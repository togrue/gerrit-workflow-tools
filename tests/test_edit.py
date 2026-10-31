"""Tests for ``ger edit``."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_edit import (
    main as gedit_main,
    main_reword as greword_main,
    resolve_first_edit_attention_sha,
)
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.git_run import git
from tests.change_store import ChangeStore
from tests.cli_gerrit_mocks import build_details_by_change_id, stack_rows_mb_to_head
from tests.conftest import run_cli


def _configure_repo(repo: Path) -> None:
    git("config", "gerrit.webUrl", "https://g.example", cwd=repo)


def test_gedit_help(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, _err = run_cli(stack_repo, gedit_main, ["--help"], monkeypatch, catch_sys_exit=True)
    assert code == 0
    assert "[REV]" in out
    assert "reword" in out.lower()
    assert "--first-attention-commit" in out
    assert "--next" in out
    assert "--cursor" in out


def test_greword_help(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, _err = run_cli(stack_repo, greword_main, ["--help"], monkeypatch, catch_sys_exit=True)
    assert code == 0
    assert "ger reword" in out
    assert "--edit" in out
    assert "--drop" in out


def test_resolve_first_edit_attention_oldest_ci_failed(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    overrides: list[dict] = [{}] * len(rows)
    overrides[0] = {"verified": -1}
    if len(overrides) > 1:
        overrides[-1] = {"unresolved_comment_count": 3}
    details = build_details_by_change_id(rows, per_index_overrides=overrides)
    sha = resolve_first_edit_attention_sha(
        stack_repo, settings=Settings.from_cwd(stack_repo), gerrit=ChangeStore(details)
    )
    assert sha == rows[0].sha


def test_resolve_first_edit_attention_none_when_green(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from gerrit_workflow_tools.core.git_run import GitError

    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    with pytest.raises(GitError, match="no commit needs edit attention"):
        resolve_first_edit_attention_sha(
            stack_repo, settings=Settings.from_cwd(stack_repo), gerrit=ChangeStore(details)
        )


def _fake_rebase_capture(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    captured: dict[str, str] = {}
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if isinstance(cmd, list) and len(cmd) >= 2 and cmd[0] == "git" and cmd[1] == "rebase":
            captured["full_sha"] = kwargs["env"]["GEDIT_FULL_SHA"]
            return subprocess.CompletedProcess(cmd, 0)
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)
    return captured


def test_gedit_first_attention_commit_starts_rebase(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    overrides: list[dict] = [{}] * len(rows)
    overrides[0] = {"unresolved_comment_count": 2}
    details = build_details_by_change_id(rows, per_index_overrides=overrides)
    captured = _fake_rebase_capture(monkeypatch)
    code, _out, err = run_cli(
        stack_repo, gedit_main, ["--first-attention-commit"], monkeypatch, gerrit=ChangeStore(details)
    )
    assert code == 0, err
    assert captured["full_sha"] == rows[0].sha


def test_gedit_bare_resume_starts_at_oldest(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    overrides: list[dict] = [{}] * len(rows)
    overrides[0] = {"unresolved_comment_count": 2}
    details = build_details_by_change_id(rows, per_index_overrides=overrides)
    captured = _fake_rebase_capture(monkeypatch)
    code, _out, err = run_cli(stack_repo, gedit_main, [], monkeypatch, gerrit=ChangeStore(details))
    assert code == 0, err
    assert captured["full_sha"] == rows[0].sha
    from gerrit_workflow_tools.core.attention_cursor import load_branch_cursor
    from gerrit_workflow_tools.core.git_state import resolve_working_branch

    settings = Settings.from_cwd(stack_repo)
    branch = resolve_working_branch(stack_repo, settings=settings)
    assert branch is not None
    cursor = load_branch_cursor(settings, branch)
    assert cursor is not None
    assert cursor.change_id == rows[0].change_id


def test_gedit_next_skips_cursor_still_needing_attention(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    assert len(rows) >= 2
    overrides: list[dict] = [{}] * len(rows)
    overrides[0] = {"unresolved_comment_count": 2}
    overrides[1] = {"verified": -1}
    details = build_details_by_change_id(rows, per_index_overrides=overrides)
    store = ChangeStore(details)
    # Seed cursor on the oldest (still needs attention).
    captured = _fake_rebase_capture(monkeypatch)
    code, _out, err = run_cli(stack_repo, gedit_main, [], monkeypatch, gerrit=store)
    assert code == 0, err
    assert captured["full_sha"] == rows[0].sha
    captured.clear()
    code, _out, err = run_cli(stack_repo, gedit_main, ["-n"], monkeypatch, gerrit=store)
    assert code == 0, err
    assert captured["full_sha"] == rows[1].sha


def test_gedit_cursor_flag_prints_status(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    overrides: list[dict] = [{}] * len(rows)
    overrides[0] = {"unresolved_comment_count": 1}
    details = build_details_by_change_id(rows, per_index_overrides=overrides)
    _fake_rebase_capture(monkeypatch)
    run_cli(stack_repo, gedit_main, ["--first-attention-commit"], monkeypatch, gerrit=ChangeStore(details))
    code, out, err = run_cli(stack_repo, gedit_main, ["--cursor"], monkeypatch, gerrit=ChangeStore(details))
    assert code == 0, err
    assert rows[0].change_id in out
    assert "editAttention: True" in out


# -- Semantic exit codes, shared with `ger fix` --------------------------------------


def test_gedit_unknown_change_id_exits_not_found(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Used to exit 7 (GIT) because resolution errors were wrapped in GitError."""
    from gerrit_workflow_tools.cli_common import ExitCode

    code, _out, err = run_cli(stack_repo, gedit_main, ["I" + "f" * 40], monkeypatch)
    assert code == ExitCode.NOT_FOUND
    assert "no commit in current stack" in err


def test_gedit_ambiguous_change_id_exits_ambiguous(dup_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from gerrit_workflow_tools.cli_common import ExitCode
    from tests.fixtures import _cid

    code, _out, err = run_cli(dup_repo, gedit_main, [_cid("a")], monkeypatch)
    assert code == ExitCode.AMBIGUOUS
    assert "ambiguous" in err.lower()


def test_gedit_rejects_a_commit_below_the_stack(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`ger edit` rewrites a stack commit, so the upstream tip is out of range."""
    from gerrit_workflow_tools.cli_common import ExitCode
    from gerrit_workflow_tools.core.git_run import git_out

    upstream_tip = git_out("rev-parse", "@{upstream}", cwd=stack_repo)
    code, _out, err = run_cli(stack_repo, gedit_main, [upstream_tip], monkeypatch)
    assert code == ExitCode.NOT_FOUND
    assert "not in the current local stack" in err


def test_greword_shares_the_same_exit_codes(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from gerrit_workflow_tools.cli_common import ExitCode

    code, _out, err = run_cli(stack_repo, greword_main, ["I" + "f" * 40], monkeypatch)
    assert code == ExitCode.NOT_FOUND
    assert "no commit in current stack" in err


def test_gedit_next_runs_show_only_when_edit_next_show_is_set(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    assert len(rows) >= 2
    overrides: list[dict] = [{}] * len(rows)
    overrides[0] = {"unresolved_comment_count": 2}
    overrides[1] = {"verified": -1}
    details = build_details_by_change_id(rows, per_index_overrides=overrides)
    store = ChangeStore(details)
    calls: list[list[str]] = []

    def _run_show(argv: list[str] | None = None, *, gerrit: object | None = None) -> int:
        assert gerrit is store
        calls.append(list(argv or []))
        return 1

    monkeypatch.setattr("gerrit_workflow_tools.cli_ger.run_show", _run_show)
    _fake_rebase_capture(monkeypatch)

    code, _out, err = run_cli(stack_repo, gedit_main, ["-n"], monkeypatch, gerrit=store)
    assert code == 0, err
    assert calls == []

    git("config", "gerrit.editNextShow", "true", cwd=stack_repo)
    code, _out, err = run_cli(stack_repo, gedit_main, ["-n"], monkeypatch, gerrit=store)
    assert code == 0, err
    assert calls == [[rows[1].sha]]


def test_greword_next_ignores_edit_next_show(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    git("config", "gerrit.editNextShow", "true", cwd=stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    overrides: list[dict] = [{}] * len(rows)
    overrides[0] = {"unresolved_comment_count": 2}
    details = build_details_by_change_id(rows, per_index_overrides=overrides)
    calls: list[list[str]] = []
    monkeypatch.setattr(
        "gerrit_workflow_tools.cli_ger.run_show",
        lambda argv=None, **_kwargs: calls.append(list(argv or [])) or 0,
    )
    _fake_rebase_capture(monkeypatch)
    code, _out, err = run_cli(stack_repo, greword_main, ["-n"], monkeypatch, gerrit=ChangeStore(details))
    assert code == 0, err
    assert calls == []


def test_gedit_next_show_failure_is_returned(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from gerrit_workflow_tools.cli_common import ExitCode

    _configure_repo(stack_repo)
    git("config", "gerrit.editNextShow", "true", cwd=stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    overrides: list[dict] = [{}] * len(rows)
    overrides[0] = {"unresolved_comment_count": 2}
    details = build_details_by_change_id(rows, per_index_overrides=overrides)
    monkeypatch.setattr("gerrit_workflow_tools.cli_ger.run_show", lambda *args, **kwargs: int(ExitCode.CONFIG))
    _fake_rebase_capture(monkeypatch)
    code, _out, _err = run_cli(stack_repo, gedit_main, ["-n"], monkeypatch, gerrit=ChangeStore(details))
    assert code == int(ExitCode.CONFIG)
