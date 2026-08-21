# Spec: docu/spec/commands/log.md
# Covers: --json, --verbose, --color, warning/stop highlighting, merged side branch range

"""Tests for ``ger log`` (mocked Gerrit)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_log import main as log_main
from gerrit_workflow_tools.cli_style import ANSI_YELLOW, GERRIT_LINK_LABEL, strip_ansi
from gerrit_workflow_tools.core.annotated_stack import (
    branches_needing_upstream,
    commit_rows_in_range,
    resolve_rev_range,
)
from gerrit_workflow_tools.core.config import ConfigError, Settings
from gerrit_workflow_tools.core.gerrit_change_status import (
    LogCommit,
    PatchsetStatus,
    ReviewerAccount,
    determine_attention,
)
from gerrit_workflow_tools.core.git_run import git, git_out
from tests.change_store import ChangeStore
from tests.cli_gerrit_mocks import (
    build_details_by_change_id,
    stack_rows_mb_to_head,
)
from tests.conftest import json_stdout, run_cli
from tests.fixtures import make_repo_with_merged_side_branch


def _configure_repo(repo: Path) -> None:
    git("config", "gerrit.webUrl", "https://g.example", cwd=repo)


def test_log_help(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, _err = run_cli(stack_repo, log_main, ["--help"], monkeypatch, catch_sys_exit=True)
    assert code == 0
    assert "ger log" in out or "log" in out
    assert "REV_RANGE" in out
    assert "--json" in out
    assert "--show-change-id" in out
    assert "--show-url" in out
    assert "--hyperlinks" in out
    assert "--verbose" in out or "-v" in out
    assert "--follow-merges" in out


def test_log_url_flag_exits_zero(
    stack_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    code, _out, err = run_cli(stack_repo, log_main, ["--url"], monkeypatch, gerrit=ChangeStore(details))
    assert code in (0, 1), (code, err)


def test_log_default_text_contains_commit_lines_and_summary(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    code, out, err = run_cli(stack_repo, log_main, [], monkeypatch, gerrit=ChangeStore(details))
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
    git("config", "gerrit.stopPattern", r"^does-not-match$", cwd=stack_repo)
    git("config", "gerrit.warningPattern", first_subject, cwd=stack_repo)
    details = build_details_by_change_id(rows)
    code, out, err = run_cli(stack_repo, log_main, ["--color", "always"], monkeypatch, gerrit=ChangeStore(details))
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
    code, out, err = run_cli(
        stack_repo, log_main, ["--verbose", "--color=never"], monkeypatch, gerrit=ChangeStore(details)
    )
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
    code, out, err = run_cli(stack_repo, log_main, ["--json"], monkeypatch, gerrit=ChangeStore(details))
    assert code == 0, err
    data = json_stdout(out)
    assert "stack" in data
    assert "commits" in data
    commits = data["commits"]
    assert isinstance(commits, list)
    assert len(commits) == len(rows)
    for item in commits:
        assert "sha" in item
        assert "patchset_status" in item
        assert "attention_reasons" in item
        assert "change_id" in item


def test_log_json_contract_required_keys_and_types(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Machine output contract: required keys and types per commit (see docu/spec/commands/log.md)."""
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    code, out, err = run_cli(
        stack_repo, log_main, ["--json", "--color=never"], monkeypatch, gerrit=ChangeStore(details)
    )
    assert code in (0, 1), err
    data = json_stdout(out)
    commits = data["commits"]
    assert isinstance(commits, list) and commits
    required_str = ("sha", "summary", "patchset_status", "change_id", "change_status")
    required_bool = ("pushed", "submittable", "abandoned")
    optional_bool = ("merged_equivalent",)
    required_list = ("attention_reasons", "ci_failures")
    for item in commits:
        for key in required_str:
            assert key in item
            assert isinstance(item[key], str), key
        for key in required_bool:
            assert key in item
            assert isinstance(item[key], bool), key
        for key in optional_bool:
            assert key in item
            assert item[key] is None or isinstance(item[key], bool), key
        for key in required_list:
            assert key in item
            assert isinstance(item[key], list), key
        assert "verified" in item
        assert "code_review" in item
        assert "comments_unresolved" in item
        assert isinstance(item["comments_unresolved"], int)


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


def test_determine_attention_missing_change_id() -> None:
    commit = LogCommit(
        sha="a" * 40,
        short_sha="abc1234",
        summary="subj",
        change_id=None,
        pushed=False,
        abandoned=False,
        patchset_status=PatchsetStatus.ABSENT,
        verified=None,
        code_review=None,
        comments_unresolved=0,
    )
    reasons = determine_attention(commit, chain_blocked=False)
    assert reasons == ["missing-change-id"]


def test_determine_attention_not_pushed_when_change_id_present() -> None:
    commit = LogCommit(
        sha="a" * 40,
        short_sha="abc1234",
        summary="subj",
        change_id="I" + "a" * 40,
        pushed=False,
        abandoned=False,
        patchset_status=PatchsetStatus.ABSENT,
        verified=None,
        code_review=None,
        comments_unresolved=0,
    )
    reasons = determine_attention(commit, chain_blocked=False)
    assert reasons == ["not-pushed"]
    assert "missing-change-id" not in reasons


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
    code, out, err = run_cli(stack_repo, log_main, [], monkeypatch, gerrit=ChangeStore(details))
    assert code == 1, err
    assert "no reviewers" in out
    code, out, err = run_cli(stack_repo, log_main, ["--json"], monkeypatch, gerrit=ChangeStore(details))
    assert code == 1, err
    data = json_stdout(out)
    assert any("no-reviewers" in item.get("attention_reasons", []) for item in data["commits"])


def test_log_explicit_revset(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    mb = git_out("merge-base", "main", "HEAD", cwd=stack_repo)
    revset = f"{mb}..HEAD"
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    code, out, err = run_cli(stack_repo, log_main, [revset], monkeypatch, gerrit=ChangeStore(details))
    assert code == 0, err
    assert "summary:" in out


def test_log_invalid_revset_returns_error(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    code, out, err = run_cli(stack_repo, log_main, ["not-a-real-revision"], monkeypatch)
    assert code == ExitCode.GIT
    assert out == ""
    assert "error:" in err.lower()


def test_log_missing_upstream_non_tty_prints_setup_hint(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    git("branch", "--unset-upstream", cwd=stack_repo, check=False)
    code, out, err = run_cli(stack_repo, log_main, [], monkeypatch)
    assert code == 1
    assert out == ""
    assert "No upstream configured for branch" in err
    assert "git branch --set-upstream-to=" in err
    assert "git log failed" not in err


def test_log_missing_gerrit_url(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "gerrit_workflow_tools.core.gerrit.service.resolve_gerrit_web_base",
        lambda _cwd: (_ for _ in ()).throw(ConfigError("missing gerrit.webUrl")),
    )
    code, _out, err = run_cli(stack_repo, log_main, [], monkeypatch)
    assert code == ExitCode.CONFIG
    assert "error" in err.lower()


def test_log_show_change_id_appends_token(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    code, out, err = run_cli(
        stack_repo, log_main, ["--show-change-id", "--color=never"], monkeypatch, gerrit=ChangeStore(details)
    )
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
    code, out, err = run_cli(stack_repo, log_main, ["--color=never"], monkeypatch, gerrit=ChangeStore(details))
    assert code == 1, err
    assert _unicode_strikethrough(rows[-1].subject) in out


def test_log_file_redirect_encodes_unicode(
    stack_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ger log > output-file.txt`` must not raise UnicodeEncodeError on legacy encodings.

    Redirected stdout often uses the locale encoding (e.g. cp1252 on Windows). Abandoned
    summaries use combining strikethrough (U+0336), which is not encodable there.
    """
    import io
    import sys

    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    overrides: list[dict] = [{}] * len(rows)
    overrides[-1] = {"status": "ABANDONED"}
    details = build_details_by_change_id(rows, per_index_overrides=overrides)

    out_path = tmp_path / "output-file.txt"
    err_buf = io.StringIO()
    monkeypatch.chdir(stack_repo)
    monkeypatch.setattr(sys, "stderr", err_buf)
    with out_path.open("w", encoding="cp1252", errors="strict", newline="\n") as out_f:
        monkeypatch.setattr(sys, "stdout", out_f)
        code = log_main(["--color=never"], gerrit=ChangeStore(details))

    assert code == 1, err_buf.getvalue()
    text = out_path.read_text(encoding="utf-8")
    assert "summary:" in text
    assert _unicode_strikethrough(rows[-1].subject) in text


def test_log_json_includes_abandoned(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    overrides = [{}] * len(rows)
    overrides[-1] = {"status": "ABANDONED"}
    details = build_details_by_change_id(rows, per_index_overrides=overrides)
    code, out, err = run_cli(stack_repo, log_main, ["--json"], monkeypatch, gerrit=ChangeStore(details))
    assert code == 1, err
    data = json_stdout(out)
    commits = data["commits"]
    assert commits[-1]["abandoned"] is True
    assert commits[0]["abandoned"] is False


def test_log_config_default_show_url(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    git("config", "gerrit.logShowUrl", "true", cwd=stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    code, out, err = run_cli(stack_repo, log_main, ["--color=never"], monkeypatch, gerrit=ChangeStore(details))
    assert code == 0, err
    assert "g.example" in out or "/+/" in out


def test_log_hyperlinks_always_shows_open_in_gerrit(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """OSC 8 terminals get a compact ``Open in gerrit`` link without ``--url``."""
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    code, out, err = run_cli(
        stack_repo,
        log_main,
        ["--hyperlinks", "always", "--color=never"],
        monkeypatch,
        gerrit=ChangeStore(details),
    )
    assert code == 0, err
    assert "\x1b]8;;https://gerrit.example" in out
    visible = strip_ansi(out)
    assert GERRIT_LINK_LABEL in visible
    assert "https://gerrit.example" not in visible


def test_log_default_omits_raw_url(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without hyperlinks or ``--url``, raw Gerrit addresses stay off (they are noisy)."""
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    code, out, err = run_cli(
        stack_repo,
        log_main,
        ["--hyperlinks", "never", "--color=never"],
        monkeypatch,
        gerrit=ChangeStore(details),
    )
    assert code == 0, err
    assert "g.example" not in out
    assert "/+/" not in out
    assert GERRIT_LINK_LABEL not in out


def test_log_hyperlinks_verbose_uses_label(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    code, out, err = run_cli(
        stack_repo,
        log_main,
        ["--verbose", "--hyperlinks", "always", "--color=never"],
        monkeypatch,
        gerrit=ChangeStore(details),
    )
    assert code == 0, err
    assert "\x1b]8;;https://gerrit.example" in out
    assert GERRIT_LINK_LABEL in strip_ansi(out)
    assert "https://gerrit.example" not in strip_ansi(out)


def test_log_hyperlinks_never_keeps_raw_url(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    git("config", "gerrit.logShowUrl", "true", cwd=stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    code, out, err = run_cli(
        stack_repo,
        log_main,
        ["--hyperlinks", "never", "--color=never"],
        monkeypatch,
        gerrit=ChangeStore(details),
    )
    assert code == 0, err
    assert "\x1b]8;" not in out
    assert "g.example" in out or "/+/" in out
    assert GERRIT_LINK_LABEL not in out


def test_log_json_keeps_raw_gerrit_url_with_hyperlinks(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    code, out, err = run_cli(
        stack_repo,
        log_main,
        ["--json", "--hyperlinks", "always"],
        monkeypatch,
        gerrit=ChangeStore(details),
    )
    assert code == 0, err
    data = json_stdout(out)
    assert "\x1b]8;" not in out
    assert any((item.get("gerrit_url") or "").startswith("https://gerrit.example") for item in data["commits"])


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

    monkeypatch.setattr("gerrit_workflow_tools.core.git_state.git_out", fake_git_out)
    monkeypatch.setattr("gerrit_workflow_tools.core.stack.git", fake_git)
    monkeypatch.setattr("gerrit_workflow_tools.core.annotated_stack.resolve_working_branch", lambda _cwd, **_kw: None)
    return git_out_calls, git_calls


def test_log_rev_range_default_branch_uses_branch_upstream_range(monkeypatch: pytest.MonkeyPatch) -> None:
    cwd = Path("mock-repo")
    git_out_calls, git_calls = _install_log_git_mocks(monkeypatch, head_branch="feat/x")

    rev_range = resolve_rev_range(cwd, None, settings=Settings.from_map({}))
    assert rev_range == "feat/x@{upstream}..feat/x"

    commit_data = commit_rows_in_range(cwd, rev_range)
    assert commit_data == []

    assert [args for args, _ in git_out_calls] == [("rev-parse", "--abbrev-ref", "HEAD")]
    assert [args for args, _cwd, _check in git_calls] == [
        ("log", "--reverse", "--first-parent", "feat/x@{upstream}..feat/x", "--format=%H%x1e%h%x1e%s%x1e%B%x1e")
    ]


def test_log_rev_range_default_detached_head_uses_head_range(monkeypatch: pytest.MonkeyPatch) -> None:
    cwd = Path("mock-repo")
    git_out_calls, git_calls = _install_log_git_mocks(monkeypatch, head_branch="HEAD")

    rev_range = resolve_rev_range(cwd, None, settings=Settings.from_map({}))
    assert rev_range == "@{upstream}..HEAD"

    commit_data = commit_rows_in_range(cwd, rev_range)
    assert commit_data == []

    assert [args for args, _ in git_out_calls] == [("rev-parse", "--abbrev-ref", "HEAD")]
    assert [args for args, _cwd, _check in git_calls] == [
        ("log", "--reverse", "--first-parent", "@{upstream}..HEAD", "--format=%H%x1e%h%x1e%s%x1e%B%x1e")
    ]


def test_log_rev_range_default_rebase_branch_uses_branch_upstream_range(monkeypatch: pytest.MonkeyPatch) -> None:
    cwd = Path("mock-repo")
    git_out_calls, git_calls = _install_log_git_mocks(monkeypatch, head_branch="HEAD")
    monkeypatch.setattr(
        "gerrit_workflow_tools.core.annotated_stack.resolve_working_branch", lambda _cwd, **_kw: "feat/x"
    )

    rev_range = resolve_rev_range(cwd, None, settings=Settings.from_map({}))
    assert rev_range == "feat/x@{upstream}..feat/x"

    commit_data = commit_rows_in_range(cwd, rev_range)
    assert commit_data == []

    assert git_out_calls == []
    assert [args for args, _cwd, _check in git_calls] == [
        ("log", "--reverse", "--first-parent", "feat/x@{upstream}..feat/x", "--format=%H%x1e%h%x1e%s%x1e%B%x1e")
    ]


def test_log_rev_range_single_branch_expands_to_branch_upstream_range(monkeypatch: pytest.MonkeyPatch) -> None:
    cwd = Path("mock-repo")
    git_out_calls, git_calls = _install_log_git_mocks(monkeypatch, head_branch="unused")

    rev_range = resolve_rev_range(cwd, "bak", settings=Settings.from_map({}))
    assert rev_range == "bak@{upstream}..bak"

    commit_data = commit_rows_in_range(cwd, rev_range)
    assert commit_data == []

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

    rev_range = resolve_rev_range(cwd, arg_rev_range, settings=Settings.from_map({}))
    assert rev_range == arg_rev_range

    commit_data = commit_rows_in_range(cwd, rev_range)
    assert commit_data == []

    assert git_out_calls == []
    assert [args for args, _cwd, _check in git_calls] == [
        ("log", "--reverse", "--first-parent", arg_rev_range, "--format=%H%x1e%h%x1e%s%x1e%B%x1e")
    ]


def test_log_rev_range_follow_merges_omits_first_parent(monkeypatch: pytest.MonkeyPatch) -> None:
    cwd = Path("mock-repo")
    git_out_calls, git_calls = _install_log_git_mocks(monkeypatch, head_branch="unused")
    _ = git_out_calls

    rev_range = resolve_rev_range(cwd, "a..b", settings=Settings.from_map({}))
    assert rev_range == "a..b"

    commit_data = commit_rows_in_range(cwd, rev_range, first_parent=False)
    assert commit_data == []

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
def test_branches_needing_upstream(
    monkeypatch: pytest.MonkeyPatch,
    rev_range: str,
    head_branch: str,
    want: list[str],
) -> None:
    monkeypatch.setattr("gerrit_workflow_tools.core.annotated_stack.current_branch", lambda _cwd: head_branch)
    monkeypatch.setattr("gerrit_workflow_tools.core.annotated_stack.resolve_working_branch", lambda _cwd, **_kw: None)
    got = branches_needing_upstream(Path("mock-repo"), rev_range, settings=Settings.from_map({}))
    assert got == want


# ---------------------------------------------------------------------------
# --follow-merges flag (first-parent / relation-chain semantics)
# ---------------------------------------------------------------------------


def test_load_commits_in_range_default_first_parent_excludes_side_branch(tmp_path: Path) -> None:
    """
    By default ``commit_rows_in_range`` uses ``first_parent=True``, matching
    Gerrit's relation-chain semantics.  Only the 2 first-parent commits are
    returned; the 2 side-branch commits are excluded.
    """
    repo = make_repo_with_merged_side_branch(tmp_path / "r")
    from gerrit_workflow_tools.core.stack import merge_base_with_target

    _fork, _disp, target_tip = merge_base_with_target(repo)
    rev_range = f"{target_tip}..HEAD"

    commit_data = commit_rows_in_range(repo, rev_range)
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

    commit_data = commit_rows_in_range(repo, rev_range, first_parent=False)
    assert commit_data is not None
    subjects = [row.summary for row in commit_data]
    assert len(subjects) == 4, f"expected 4 commits with full-DAG traversal, got {len(subjects)}: {subjects}"
    assert sum(1 for s in subjects if "side commit" in s) == 2


def test_log_same_change_id_on_main_and_dev_shows_main_only(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Triplet-scoped overlay: main stack sees the main change, not the dev duplicate."""
    from tests.cli_gerrit_mocks import change_info_for_sha

    _configure_repo(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    assert rows
    target_row = rows[0]
    cid = target_row.change_id
    assert cid

    main_detail = change_info_for_sha(
        target_row.sha,
        cid,
        project="testproj",
        branch="main",
        number=100,
    )
    dev_detail = change_info_for_sha(
        target_row.sha,
        cid,
        project="testproj",
        branch="dev",
        number=101,
    )
    details = {
        str(main_detail["id"]): main_detail,
        str(dev_detail["id"]): dev_detail,
    }
    code, out, err = run_cli(stack_repo, log_main, ["--json"], monkeypatch, gerrit=ChangeStore(details))
    assert code in (0, 1), err
    data = json_stdout(out)
    matched = [item for item in data["commits"] if item.get("change_id") == cid]
    assert len(matched) == 1
    assert matched[0]["pushed"] is True
    assert matched[0]["patchset_status"] != "absent"
    assert all(item.get("_number", item.get("gerrit_url", "")) != 101 for item in data["commits"])
