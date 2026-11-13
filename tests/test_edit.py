"""Tests for ``ger edit``."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_edit import (
    main as gedit_main,
    main_reword as greword_main,
    resolve_first_edit_attention_sha,
)
from gerrit_workflow_tools.cli_rebase import main as grebase_main
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.git_run import git, git_out
from tests.change_store import ChangeStore
from tests.cli_gerrit_mocks import build_details_by_change_id, stack_rows_mb_to_head
from tests.conftest import run_cli
from tests.helpers import write_rebase_head


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
    assert rows[0].change_id is not None
    assert rows[0].change_id in out
    assert "editAttention: True" in out


# -- Semantic exit codes, shared with `ger fix` --------------------------------------


@pytest.mark.parametrize(
    ("main_fn", "argv"),
    [
        (gedit_main, []),
        (gedit_main, ["--first-attention-commit"]),
        (greword_main, ["-n"]),
        (grebase_main, []),
    ],
)
def test_rebase_in_progress_is_refused_before_anything_moves(
    main_fn: Callable[..., int],
    argv: list[str],
    stack_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_repo(stack_repo)
    write_rebase_head(stack_repo, "feature")
    rows = stack_rows_mb_to_head(stack_repo)
    store = ChangeStore(build_details_by_change_id(rows, per_index_overrides=[{"verified": -1} for _ in rows]))

    code, _out, err = run_cli(stack_repo, main_fn, argv, monkeypatch, gerrit=store)

    assert code == ExitCode.USAGE
    assert "git rebase --continue" in err
    assert git("config", "branch.feature.gerAttentionCursor", cwd=stack_repo, check=False).returncode != 0


def test_cursor_stays_put_when_the_rebase_never_starts(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    store = ChangeStore(build_details_by_change_id(rows, per_index_overrides=[{"verified": -1} for _ in rows]))
    real_run = subprocess.run

    def failing_rebase(cmd, **kwargs):
        if isinstance(cmd, list) and cmd[:2] == ["git", "rebase"]:
            return subprocess.CompletedProcess(cmd, 128)
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", failing_rebase)
    code, _out, _err = run_cli(stack_repo, gedit_main, ["-n"], monkeypatch, gerrit=store)

    assert code == 128
    assert git("config", "branch.feature.gerAttentionCursor", cwd=stack_repo, check=False).returncode != 0


def test_greword_warns_when_the_change_id_changes(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    tip = rows[-1]
    real_run = subprocess.run

    def rewording_rebase(cmd, **kwargs):
        if isinstance(cmd, list) and cmd[:2] == ["git", "rebase"]:
            git("commit", "--amend", "--allow-empty", "-m", "no change id here", cwd=stack_repo)
            return subprocess.CompletedProcess(cmd, 0)
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", rewording_rebase)
    store = ChangeStore(build_details_by_change_id(rows))
    code, _out, err = run_cli(stack_repo, greword_main, [tip.sha], monkeypatch, gerrit=store)

    assert code == 0
    assert f"warning: Change-Id {tip.change_id} left the stack" in err


def test_gedit_cursor_needs_no_upstream_when_there_is_no_cursor(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git("branch", "--unset-upstream", "feature", cwd=stack_repo, check=False)
    code, out, _err = run_cli(stack_repo, gedit_main, ["--cursor"], monkeypatch)
    assert code == 0
    assert "attention cursor: (none)" in out


def test_attention_walk_end_has_its_own_exit_code(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    store = ChangeStore(build_details_by_change_id(stack_rows_mb_to_head(stack_repo)))
    code, _out, err = run_cli(stack_repo, gedit_main, ["-n"], monkeypatch, gerrit=store)
    assert code == ExitCode.NO_EDIT_ATTENTION
    assert "no commit needs edit attention" in err


def test_set_cursor_twice_records_one_history_entry(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    store = ChangeStore(build_details_by_change_id(rows))

    run_cli(stack_repo, gedit_main, ["--set-cursor", rows[0].sha], monkeypatch, gerrit=store)
    _code, out, _err = run_cli(stack_repo, gedit_main, ["--set-cursor", rows[0].sha], monkeypatch, gerrit=store)

    assert "already at" in out
    _code, out, _err = run_cli(stack_repo, gedit_main, ["--cursor-history"], monkeypatch, gerrit=store)
    assert len(out.splitlines()) == 1


def test_gedit_says_where_it_stopped_and_what_comes_next(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    real_run = subprocess.run

    def stopping_rebase(cmd, **kwargs):
        if isinstance(cmd, list) and cmd[:2] == ["git", "rebase"]:
            write_rebase_head(stack_repo, "feature")
            return subprocess.CompletedProcess(cmd, 0)
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", stopping_rebase)
    store = ChangeStore(build_details_by_change_id(rows))
    code, _out, err = run_cli(stack_repo, gedit_main, [rows[0].sha], monkeypatch, gerrit=store)

    assert code == 0
    assert f"stopped at {rows[0].short_sha} {rows[0].subject}" in err
    assert err.rstrip().endswith(
        "next: amend it, then `git rebase --continue` to finish; "
        "`ger edit -n` finishes and goes on to the next commit needing attention"
    )


def test_gedit_unknown_change_id_exits_not_found(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Used to exit 7 (GIT) because resolution errors were wrapped in GitError."""
    from gerrit_workflow_tools.cli_common import ExitCode

    code, _out, err = run_cli(stack_repo, gedit_main, ["I" + "f" * 40], monkeypatch)
    assert code == ExitCode.NOT_FOUND
    assert "no commit in the local stack has that Change-Id" in err


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
    assert "is not in the local stack" in err


def test_greword_shares_the_same_exit_codes(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from gerrit_workflow_tools.cli_common import ExitCode

    code, _out, err = run_cli(stack_repo, greword_main, ["I" + "f" * 40], monkeypatch)
    assert code == ExitCode.NOT_FOUND
    assert "no commit in the local stack has that Change-Id" in err


@pytest.mark.parametrize(
    ("config", "shows"),
    [
        ({}, True),
        ({"ger.showAfterEdit": "false"}, False),
        ({"gerrit.editNextShow": "false"}, False),
        ({"gerrit.editNextShow": "true"}, True),
        ({"gerrit.editNextShow": "false", "ger.showAfterEdit": "true"}, True),
    ],
)
@pytest.mark.parametrize("walk", [["-n"], ["--first-attention-commit"]])
def test_gedit_walk_runs_show_unless_show_after_edit_is_off(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch, config: dict[str, str], shows: bool, walk: list[str]
) -> None:
    """Issue #33: on by default, for --next and --first-attention-commit; the old key still turns it off."""
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

    for key, value in config.items():
        git("config", key, value, cwd=stack_repo)
    code, _out, err = run_cli(stack_repo, gedit_main, walk, monkeypatch, gerrit=store)
    assert code == 0, err
    assert calls == ([[rows[0].sha]] if shows else [])


def test_greword_next_ignores_show_after_edit(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    git("config", "ger.showAfterEdit", "true", cwd=stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    overrides: list[dict] = [{}] * len(rows)
    overrides[0] = {"unresolved_comment_count": 2}
    details = build_details_by_change_id(rows, per_index_overrides=overrides)
    calls: list[list[str]] = []

    def _run_show(argv: list[str] | None = None, **_kwargs: object) -> int:
        calls.append(list(argv or []))
        return 0

    monkeypatch.setattr("gerrit_workflow_tools.cli_ger.run_show", _run_show)
    _fake_rebase_capture(monkeypatch)
    code, _out, err = run_cli(stack_repo, greword_main, ["-n"], monkeypatch, gerrit=ChangeStore(details))
    assert code == 0, err
    assert calls == []


def test_gedit_next_continues_a_stopped_rebase_then_stops_at_the_next_commit(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gerrit_workflow_tools.core.attention_cursor import load_branch_cursor
    from gerrit_workflow_tools.core.rebase_state import rebase_in_progress

    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    overrides: list[dict] = [{}] * len(rows)
    overrides[0] = {"unresolved_comment_count": 1}
    overrides[1] = {"verified": -1}
    store = ChangeStore(build_details_by_change_id(rows, per_index_overrides=overrides))

    code, _out, err = run_cli(stack_repo, gedit_main, [rows[0].sha], monkeypatch, gerrit=store)
    assert code == 0, err
    assert rebase_in_progress(stack_repo)
    assert err.rstrip().endswith(
        "next: amend it, then `git rebase --continue` to finish; "
        "`ger edit -n` finishes and goes on to the next commit needing attention"
    )

    code, _out, err = run_cli(stack_repo, gedit_main, ["-n"], monkeypatch, gerrit=store)
    assert code == 0, err
    assert "continuing the rebase" in err
    assert "stopped at" in err
    assert rows[1].subject in err
    assert rebase_in_progress(stack_repo)
    cursor = load_branch_cursor(Settings.from_cwd(stack_repo), "feature")
    assert cursor is not None
    assert cursor.change_id == rows[1].change_id


def test_gedit_next_does_not_walk_when_continue_fails(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from gerrit_workflow_tools.core.attention_cursor import load_branch_cursor
    from gerrit_workflow_tools.core.rebase_state import rebase_in_progress

    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    overrides: list[dict] = [{}] * len(rows)
    overrides[0] = {"unresolved_comment_count": 1}
    overrides[1] = {"verified": -1}
    store = ChangeStore(build_details_by_change_id(rows, per_index_overrides=overrides))
    code, _out, err = run_cli(stack_repo, gedit_main, [rows[0].sha], monkeypatch, gerrit=store)
    assert code == 0, err

    real_run = subprocess.run
    started: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        if isinstance(cmd, list) and cmd[:3] == ["git", "rebase", "--continue"]:
            return subprocess.CompletedProcess(cmd, 1)
        if isinstance(cmd, list) and cmd[:2] == ["git", "rebase"]:
            started.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0)
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)
    code, _out, err = run_cli(stack_repo, gedit_main, ["-n"], monkeypatch, gerrit=store)

    assert code == 1
    assert "continuing the rebase" in err
    assert "`ger edit -n` again" in err
    assert started == []
    assert rebase_in_progress(stack_repo)
    cursor = load_branch_cursor(Settings.from_cwd(stack_repo), "feature")
    assert cursor is not None
    assert cursor.change_id == rows[0].change_id


def test_gedit_next_stops_when_continue_leaves_the_rebase_running(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gerrit_workflow_tools.core.rebase_state import rebase_in_progress

    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    overrides: list[dict] = [{}] * len(rows)
    overrides[0] = {"unresolved_comment_count": 1}
    store = ChangeStore(build_details_by_change_id(rows, per_index_overrides=overrides))
    code, _out, _err = run_cli(stack_repo, gedit_main, [rows[0].sha], monkeypatch, gerrit=store)
    assert code == 0

    real_run = subprocess.run
    started: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        if isinstance(cmd, list) and cmd[:3] == ["git", "rebase", "--continue"]:
            return subprocess.CompletedProcess(cmd, 0)
        if isinstance(cmd, list) and cmd[:2] == ["git", "rebase"]:
            started.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0)
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)
    code, _out, err = run_cli(stack_repo, gedit_main, ["-n"], monkeypatch, gerrit=store)

    assert code == ExitCode.GIT
    assert "rebase is still in progress" in err
    assert started == []
    assert rebase_in_progress(stack_repo)


def test_gedit_next_show_failure_is_returned(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from gerrit_workflow_tools.cli_common import ExitCode

    _configure_repo(stack_repo)
    git("config", "ger.showAfterEdit", "true", cwd=stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    overrides: list[dict] = [{}] * len(rows)
    overrides[0] = {"unresolved_comment_count": 2}
    details = build_details_by_change_id(rows, per_index_overrides=overrides)
    monkeypatch.setattr("gerrit_workflow_tools.cli_ger.run_show", lambda *args, **kwargs: int(ExitCode.CONFIG))
    _fake_rebase_capture(monkeypatch)
    code, _out, _err = run_cli(stack_repo, gedit_main, ["-n"], monkeypatch, gerrit=ChangeStore(details))
    assert code == int(ExitCode.CONFIG)


def test_the_walk_says_which_commit_it_chose_before_the_editor(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    overrides: list[dict] = [{}] * len(rows)
    overrides[0] = {"unresolved_comment_count": 2}
    store = ChangeStore(build_details_by_change_id(rows, per_index_overrides=overrides))
    _fake_rebase_capture(monkeypatch)

    code, _out, err = run_cli(stack_repo, greword_main, ["--first-attention-commit"], monkeypatch, gerrit=store)

    assert code == 0, err
    assert f"rewording {rows[0].short_sha} {rows[0].subject} (oldest needing attention: 2 unresolved comments)" in err


def test_the_walk_ending_after_a_continued_rebase_is_no_error(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    overrides: list[dict] = [{}] * len(rows)
    overrides[0] = {"unresolved_comment_count": 1}
    store = ChangeStore(build_details_by_change_id(rows, per_index_overrides=overrides))
    code, _out, err = run_cli(stack_repo, gedit_main, [rows[0].sha], monkeypatch, gerrit=store)
    assert code == 0, err

    code, _out, err = run_cli(stack_repo, gedit_main, ["-n"], monkeypatch, gerrit=store)

    assert code == ExitCode.NO_EDIT_ATTENTION
    assert err.rstrip().endswith(
        "rebase finished\nno commit needs edit attention (unresolved comments or build failed)"
    )
    assert "error:" not in err


def test_drop_says_what_it_dropped_and_how_to_get_it_back(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    before = git_out("rev-parse", "HEAD", cwd=stack_repo)
    store = ChangeStore(build_details_by_change_id(rows))

    code, _out, err = run_cli(stack_repo, gedit_main, ["--drop", rows[-1].sha], monkeypatch, gerrit=store)

    assert code == 0, err
    assert f"dropped {rows[-1].short_sha} {rows[-1].subject}" in err
    undo = err.split("to get it back: ")[1].split()
    git(*undo[1:], cwd=stack_repo)
    assert git_out("rev-parse", "HEAD", cwd=stack_repo) == before


def test_reword_warning_says_how_to_keep_the_old_change(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    tip = rows[-1]
    real_run = subprocess.run

    def rewording_rebase(cmd, **kwargs):
        if isinstance(cmd, list) and cmd[:2] == ["git", "rebase"]:
            git("commit", "--amend", "--allow-empty", "-m", "no change id here", cwd=stack_repo)
            return subprocess.CompletedProcess(cmd, 0)
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", rewording_rebase)
    code, _out, err = run_cli(
        stack_repo, greword_main, [tip.sha], monkeypatch, gerrit=ChangeStore(build_details_by_change_id(rows))
    )

    assert code == 0
    assert "The old change stays open on Gerrit" in err
    assert f"put `Change-Id: {tip.change_id}` back with `ger reword" in err
