"""Tests for ``ger log`` (mocked Gerrit)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_log import (
    _load_commits_in_range,
    _resolve_rev_range,
    rev_range_needs_upstream_resolution,
)
from gerrit_workflow_tools.cli_log import main as log_main
from gerrit_workflow_tools.cli_style import ANSI_YELLOW
from gerrit_workflow_tools.core.config import clear_gerrit_git_config_cache
from gerrit_workflow_tools.core.gerrit_change_status import (
    LogCommit,
    PatchsetStatus,
    ReviewerAccount,
    determine_attention,
)
from gerrit_workflow_tools.core.git_run import git, git_out
from tests.cli_gerrit_mocks import (
    build_details_by_change_id,
    patch_gerrit_client_for_queries,
    stack_rows_mb_to_head,
)
from tests.conftest import json_stdout, run_cli
from tests.fixtures import make_repo_with_merged_side_branch


def _configure_repo(repo: Path) -> None:
    git("config", "gerrit.webUrl", "https://g.example", cwd=repo)
    clear_gerrit_git_config_cache()


def test_log_help(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, _err = run_cli(stack_repo, log_main, ["--help"], monkeypatch, catch_sys_exit=True)
    assert code == 0
    assert "ger log" in out or "log" in out
    assert "REV_RANGE" in out
    assert "--filter-attention" in out
    assert "--json" in out
    assert "--show-change-id" in out
    assert "--show-url" in out
    assert "--verbose" in out or "-v" in out
    assert "--follow-merges" in out


@pytest.mark.parametrize(
    "argv_extra",
    [
        [],
        ["--filter-attention"],
        ["--filter-attention", "-v"],
        ["--filter-attention", "--url"],
        ["--filter-attention", "--color=never"],
    ],
)
def test_log_smoke_argv_exits_zero(
    stack_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    argv_extra: list[str],
) -> None:
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_log", details_by_change_id=details):
        code, _out, err = run_cli(stack_repo, log_main, argv_extra, monkeypatch)
    assert code in (0, 1), (code, err)


def test_log_default_text_contains_commit_lines_and_summary(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_log", details_by_change_id=details):
        code, out, err = run_cli(stack_repo, log_main, [], monkeypatch)
    assert code == 0, err
    assert "summary:" in out
    assert "ready" in out and "/" in out
    for c in rows:
        assert c.short_sha in out
        assert c.subject in out


def test_log_highlights_warning_pattern_in_summary(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    first_subject = rows[0].subject
    git("config", "--unset-all", "gerrit.stopPattern", cwd=stack_repo, check=False)
    git("config", "--add", "gerrit.stopPattern", r"^does-not-match$", cwd=stack_repo)
    git("config", "--unset-all", "gerrit.warningPattern", cwd=stack_repo, check=False)
    git("config", "--add", "gerrit.warningPattern", first_subject, cwd=stack_repo)
    clear_gerrit_git_config_cache()
    details = build_details_by_change_id(rows)
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_log", details_by_change_id=details):
        code, out, err = run_cli(stack_repo, log_main, ["--color", "always"], monkeypatch)
    assert code == 0, err
    assert ANSI_YELLOW in out
    assert first_subject in out


def test_log_full_text_uses_separate_detail_lines(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``--verbose``: oneline row with attention; indented URL; no duplicate comment-count detail line."""
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    overrides: list[dict] = [{} for _ in rows]
    overrides[0] = {"verified": -1, "submittable": False}
    overrides[1] = {"verified": 0, "cr": 0, "unresolved_comment_count": 2, "submittable": False}
    overrides[-1] = {"status": "ABANDONED", "submittable": False}
    details = build_details_by_change_id(rows, per_index_overrides=overrides)
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_log", details_by_change_id=details):
        code, out, err = run_cli(stack_repo, log_main, ["--verbose", "--color=never"], monkeypatch)
    assert code == 1, err
    assert "v? " in out
    assert "cr? " in out
    assert "# submittable" in out
    assert "build failed" in out
    assert "2 unresolved comments" in out
    assert "# comments:" not in out
    assert "# abandoned" in out
    assert "g.example" in out or "/+/" in out
    assert "✓" not in out


def test_log_json_default_lists_all_commits(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_log", details_by_change_id=details):
        code, out, err = run_cli(stack_repo, log_main, ["--json"], monkeypatch)
    assert code == 0, err
    data = json_stdout(out)
    assert isinstance(data, list)
    assert len(data) == len(rows)
    for item in data:
        assert "sha" in item
        assert "patchset_status" in item
        assert "attention_reasons" in item
        assert "change_id" in item


def test_log_filter_attention_hides_when_all_green(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With no attention, ``--filter-attention`` prints no per-commit lines."""
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_log", details_by_change_id=details):
        code, out, err = run_cli(stack_repo, log_main, ["--filter-attention"], monkeypatch)
    assert code == 0, err
    assert "summary:" in out
    assert "... " in out
    assert "non-attention commits" in out
    assert rows[0].short_sha not in out
    assert rows[0].subject not in out


def test_determine_attention_no_reviewers_when_empty() -> None:
    commit = LogCommit(
        sha="a" * 40,
        short_sha="abc1234",
        summary="subj",
        change_id="I" + "a" * 40,
        pushed=True,
        abandoned=False,
        patchset_status=PatchsetStatus.ACTIVE,
        verified=1,
        code_review=2,
        comments_unresolved=0,
        submittable=True,
        reviewers=[],
    )
    reasons = determine_attention(commit, chain_blocked=False)
    assert "no-reviewers" in reasons


def test_determine_attention_no_reviewers_absent_when_assigned() -> None:
    commit = LogCommit(
        sha="a" * 40,
        short_sha="abc1234",
        summary="subj",
        change_id="I" + "a" * 40,
        pushed=True,
        abandoned=False,
        patchset_status=PatchsetStatus.ACTIVE,
        verified=1,
        code_review=2,
        comments_unresolved=0,
        submittable=True,
        reviewers=[ReviewerAccount(slug="alice")],
    )
    reasons = determine_attention(commit, chain_blocked=False)
    assert "no-reviewers" not in reasons


def test_log_no_reviewers_shown_in_attention(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    overrides: list[dict] = [{} for _ in rows]
    overrides[0] = {"reviewers": []}
    details = build_details_by_change_id(rows, per_index_overrides=overrides)
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_log", details_by_change_id=details):
        code, out, err = run_cli(stack_repo, log_main, [], monkeypatch)
    assert code == 1, err
    assert "no reviewers" in out
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_log", details_by_change_id=details):
        code, out, err = run_cli(stack_repo, log_main, ["--json"], monkeypatch)
    assert code == 1, err
    data = json_stdout(out)
    assert any("no-reviewers" in item.get("attention_reasons", []) for item in data)


def test_log_filter_attention_shows_only_attention(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With ``--filter-attention``, only attention commits appear."""
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    overrides: list[dict] = [{}] * len(rows)
    if overrides:
        overrides[-1] = {"cr": 1}
    details = build_details_by_change_id(rows, per_index_overrides=overrides)
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_log", details_by_change_id=details):
        code, out, err = run_cli(stack_repo, log_main, ["--filter-attention"], monkeypatch)
    assert code == 1, err
    last = rows[-1]
    assert last.short_sha in out
    assert "cr+1" in out


def test_log_explicit_revset(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    mb = git_out("merge-base", "main", "HEAD", cwd=stack_repo)
    revset = f"{mb}..HEAD"
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_log", details_by_change_id=details):
        code, out, err = run_cli(stack_repo, log_main, [revset], monkeypatch)
    assert code == 0, err
    assert "summary:" in out


def test_log_invalid_revset_returns_error(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    code, out, err = run_cli(stack_repo, log_main, ["not-a-real-revision"], monkeypatch)
    assert code == 2
    assert out == ""
    assert "error:" in err.lower()


def test_log_missing_gerrit_url(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "gerrit_workflow_tools.cli_log.resolve_gerrit_web_base",
        lambda _cwd: (_ for _ in ()).throw(ValueError("missing gerrit.webUrl")),
    )
    code, _out, err = run_cli(stack_repo, log_main, [], monkeypatch)
    assert code == 3
    assert "error" in err.lower()


def test_log_show_change_id_appends_token(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_log", details_by_change_id=details):
        code, out, err = run_cli(stack_repo, log_main, ["--show-change-id", "--color=never"], monkeypatch)
    assert code == 0, err
    cid = rows[0].change_id
    assert cid
    assert cid[:12] in out


def _unicode_strikethrough(s: str) -> str:
    return "".join(f"{c}\u0336" for c in s)


def test_log_abandoned_strikes_summary(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Abandoned Gerrit changes render the subject with strike-through (no TTY: combining chars)."""
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    overrides: list[dict] = [{}] * len(rows)
    if overrides:
        overrides[-1] = {"status": "ABANDONED"}
    details = build_details_by_change_id(rows, per_index_overrides=overrides)
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_log", details_by_change_id=details):
        code, out, err = run_cli(stack_repo, log_main, ["--color=never"], monkeypatch)
    assert code == 1, err
    assert _unicode_strikethrough(rows[-1].subject) in out


def test_log_json_includes_abandoned(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    overrides = [{}] * len(rows)
    overrides[-1] = {"status": "ABANDONED"}
    details = build_details_by_change_id(rows, per_index_overrides=overrides)
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_log", details_by_change_id=details):
        code, out, err = run_cli(stack_repo, log_main, ["--json"], monkeypatch)
    assert code == 1, err
    data = json_stdout(out)
    assert data[-1]["abandoned"] is True
    assert data[0]["abandoned"] is False


def test_log_config_default_show_url(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    git("config", "gerrit.logShowUrl", "true", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_log", details_by_change_id=details):
        code, out, err = run_cli(stack_repo, log_main, ["--color=never"], monkeypatch)
    assert code == 0, err
    assert "g.example" in out or "/+/" in out


# ---------------------------------------------------------------------------
# Revision range resolution argv behavior
# ---------------------------------------------------------------------------


def _install_log_git_mocks(
    monkeypatch: pytest.MonkeyPatch,
    *,
    head_branch: str,
) -> tuple[list[tuple[tuple[str, ...], Path]], list[tuple[tuple[str, ...], Path, bool]]]:
    git_out_calls: list[tuple[tuple[str, ...], Path]] = []
    git_calls: list[tuple[tuple[str, ...], Path, bool]] = []

    def fake_git_out(*args: str, cwd: Path | str | None = None) -> str:
        assert isinstance(cwd, Path)
        git_out_calls.append((args, cwd))
        if args == ("rev-parse", "--abbrev-ref", "HEAD"):
            return head_branch
        raise AssertionError(f"unexpected git_out call: {args}")

    def fake_git(
        *args: str,
        cwd: Path | str | None = None,
        env: dict[str, str] | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        assert isinstance(cwd, Path)
        _ = env
        git_calls.append((args, cwd, check))
        return subprocess.CompletedProcess(args=list(args), returncode=0, stdout="", stderr="")

    monkeypatch.setattr("gerrit_workflow_tools.core.config.git_out", fake_git_out)
    monkeypatch.setattr("gerrit_workflow_tools.core.stack.git", fake_git)
    monkeypatch.setattr("gerrit_workflow_tools.cli_log.resolve_working_branch", lambda _cwd: None)
    return git_out_calls, git_calls


def test_log_rev_range_default_branch_uses_branch_upstream_range(monkeypatch: pytest.MonkeyPatch) -> None:
    cwd = Path("mock-repo")
    git_out_calls, git_calls = _install_log_git_mocks(monkeypatch, head_branch="feat/x")

    rev_range, exit_code = _resolve_rev_range(cwd, None)
    assert exit_code is None
    assert rev_range == "feat/x@{upstream}..feat/x"

    commit_data, load_exit = _load_commits_in_range(cwd, rev_range)
    assert load_exit == 0
    assert commit_data is None

    assert [args for args, _ in git_out_calls] == [("rev-parse", "--abbrev-ref", "HEAD")]
    assert [args for args, _cwd, _check in git_calls] == [
        ("log", "--reverse", "--first-parent", "feat/x@{upstream}..feat/x", "--format=%H%x1e%h%x1e%s%x1e%B%x1e")
    ]


def test_log_rev_range_default_detached_head_uses_head_range(monkeypatch: pytest.MonkeyPatch) -> None:
    cwd = Path("mock-repo")
    git_out_calls, git_calls = _install_log_git_mocks(monkeypatch, head_branch="HEAD")

    rev_range, exit_code = _resolve_rev_range(cwd, None)
    assert exit_code is None
    assert rev_range == "@{upstream}..HEAD"

    commit_data, load_exit = _load_commits_in_range(cwd, rev_range)
    assert load_exit == 0
    assert commit_data is None

    assert [args for args, _ in git_out_calls] == [("rev-parse", "--abbrev-ref", "HEAD")]
    assert [args for args, _cwd, _check in git_calls] == [
        ("log", "--reverse", "--first-parent", "@{upstream}..HEAD", "--format=%H%x1e%h%x1e%s%x1e%B%x1e")
    ]


def test_log_rev_range_default_rebase_branch_uses_branch_upstream_range(monkeypatch: pytest.MonkeyPatch) -> None:
    cwd = Path("mock-repo")
    git_out_calls, git_calls = _install_log_git_mocks(monkeypatch, head_branch="HEAD")
    monkeypatch.setattr("gerrit_workflow_tools.cli_log.resolve_working_branch", lambda _cwd: "feat/x")

    rev_range, exit_code = _resolve_rev_range(cwd, None)
    assert exit_code is None
    assert rev_range == "feat/x@{upstream}..feat/x"

    commit_data, load_exit = _load_commits_in_range(cwd, rev_range)
    assert load_exit == 0
    assert commit_data is None

    assert git_out_calls == []
    assert [args for args, _cwd, _check in git_calls] == [
        ("log", "--reverse", "--first-parent", "feat/x@{upstream}..feat/x", "--format=%H%x1e%h%x1e%s%x1e%B%x1e")
    ]


def test_log_rev_range_single_branch_expands_to_branch_upstream_range(monkeypatch: pytest.MonkeyPatch) -> None:
    cwd = Path("mock-repo")
    git_out_calls, git_calls = _install_log_git_mocks(monkeypatch, head_branch="unused")

    rev_range, exit_code = _resolve_rev_range(cwd, "bak")
    assert exit_code is None
    assert rev_range == "bak@{upstream}..bak"

    commit_data, load_exit = _load_commits_in_range(cwd, rev_range)
    assert load_exit == 0
    assert commit_data is None

    assert git_out_calls == []
    assert [args for args, _cwd, _check in git_calls] == [
        ("log", "--reverse", "--first-parent", "bak@{upstream}..bak", "--format=%H%x1e%h%x1e%s%x1e%B%x1e")
    ]


@pytest.mark.parametrize("arg_rev_range", ["a..b", "a...b"])
def test_log_rev_range_explicit_ranges_are_forwarded_verbatim(
    monkeypatch: pytest.MonkeyPatch,
    arg_rev_range: str,
) -> None:
    cwd = Path("mock-repo")
    git_out_calls, git_calls = _install_log_git_mocks(monkeypatch, head_branch="unused")

    rev_range, exit_code = _resolve_rev_range(cwd, arg_rev_range)
    assert exit_code is None
    assert rev_range == arg_rev_range

    commit_data, load_exit = _load_commits_in_range(cwd, rev_range)
    assert load_exit == 0
    assert commit_data is None

    assert git_out_calls == []
    assert [args for args, _cwd, _check in git_calls] == [
        ("log", "--reverse", "--first-parent", arg_rev_range, "--format=%H%x1e%h%x1e%s%x1e%B%x1e")
    ]


def test_log_rev_range_follow_merges_omits_first_parent(monkeypatch: pytest.MonkeyPatch) -> None:
    cwd = Path("mock-repo")
    git_out_calls, git_calls = _install_log_git_mocks(monkeypatch, head_branch="unused")
    _ = git_out_calls

    rev_range, exit_code = _resolve_rev_range(cwd, "a..b")
    assert exit_code is None
    assert rev_range == "a..b"

    commit_data, load_exit = _load_commits_in_range(cwd, rev_range, first_parent=False)
    assert load_exit == 0
    assert commit_data is None

    assert [args for args, _cwd, _check in git_calls] == [
        ("log", "--reverse", "a..b", "--format=%H%x1e%h%x1e%s%x1e%B%x1e")
    ]


@pytest.mark.parametrize(
    ("rev_range", "head_branch", "want"),
    [
        ("@{upstream}..HEAD", "feat/x", ["feat/x"]),
        ("feat/topic@{upstream}..feat/topic", "unused", ["feat/topic"]),
        ("origin/main..HEAD", "feat/x", []),
        ("one@{upstream}...two@{upstream}", "unused", ["one", "two"]),
    ],
)
def test_rev_range_needs_upstream_resolution(
    monkeypatch: pytest.MonkeyPatch,
    rev_range: str,
    head_branch: str,
    want: list[str],
) -> None:
    monkeypatch.setattr("gerrit_workflow_tools.cli_log.current_branch", lambda _cwd: head_branch)
    monkeypatch.setattr("gerrit_workflow_tools.cli_log.resolve_working_branch", lambda _cwd: None)
    got = rev_range_needs_upstream_resolution(Path("mock-repo"), rev_range)
    assert got == want


# ---------------------------------------------------------------------------
# --follow-merges flag (first-parent / relation-chain semantics)
# ---------------------------------------------------------------------------


def test_load_commits_in_range_default_first_parent_excludes_side_branch(tmp_path: Path) -> None:
    """
    By default ``_load_commits_in_range`` uses ``first_parent=True``, matching
    Gerrit's relation-chain semantics.  Only the 2 first-parent commits are
    returned; the 2 side-branch commits are excluded.
    """
    repo = make_repo_with_merged_side_branch(tmp_path / "r")
    from gerrit_workflow_tools.core.stack import merge_base_with_target

    _fork, _disp, target_tip = merge_base_with_target(repo)
    rev_range = f"{target_tip}..HEAD"

    commit_data, exit_code = _load_commits_in_range(repo, rev_range)
    assert exit_code == 0
    assert commit_data is not None
    subjects = [row.summary for row in commit_data]
    assert len(subjects) == 2, f"expected 2 first-parent commits, got {len(subjects)}: {subjects}"
    assert any("local work" in s for s in subjects)
    assert any("Merge side branch" in s for s in subjects)
    assert not any("side commit" in s for s in subjects)


def test_load_commits_in_range_follow_merges_includes_side_branch(tmp_path: Path) -> None:
    """
    With ``first_parent=False`` (i.e. ``--follow-merges``), all 4 commits are
    returned including the 2 side-branch commits.
    """
    repo = make_repo_with_merged_side_branch(tmp_path / "r")
    from gerrit_workflow_tools.core.stack import merge_base_with_target

    _fork, _disp, target_tip = merge_base_with_target(repo)
    rev_range = f"{target_tip}..HEAD"

    commit_data, exit_code = _load_commits_in_range(repo, rev_range, first_parent=False)
    assert exit_code == 0
    assert commit_data is not None
    subjects = [row.summary for row in commit_data]
    assert len(subjects) == 4, f"expected 4 commits with full-DAG traversal, got {len(subjects)}: {subjects}"
    assert sum(1 for s in subjects if "side commit" in s) == 2
