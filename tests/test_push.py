from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gerrit_workflow_tools.cli_push import main as gpush_main
from gerrit_workflow_tools.cli_style import ANSI_YELLOW
from gerrit_workflow_tools.config import clear_gerrit_git_config_cache, set_branch_config
from gerrit_workflow_tools.git_run import git, git_out
from gerrit_workflow_tools.ready_calc import compute_ready
from tests.cli_gerrit_mocks import build_details_by_change_id, patch_gerrit_client_for_queries, stack_rows_mb_to_head
from tests.conftest import run_cli
from tests.fixtures import configure_gerrit_target


def _ref_exists(repo: Path, ref: str) -> bool:
    p = git("rev-parse", "--verify", ref, cwd=repo, check=False)
    return p.returncode == 0


def test_gpush_help(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, _err = run_cli(stack_repo, gpush_main, ["--help"], monkeypatch, catch_sys_exit=True)
    assert code == 0
    assert "gpush" in out.lower() or "ger push" in out
    assert "--dry-run" in out
    assert "--reviewers" in out
    assert "--reviewer" not in out.replace("--reviewers", "")
    assert "--show-attributes" in out
    assert "--no-show-attributes" in out
    assert "--update-last-pushed" in out
    assert "--no-update-last-pushed" in out
    assert "--no-rebase-check" in out
    assert "-i" in out


def test_gpush_dry_run_prints_refs_for_and_push_command(stack_repo, monkeypatch):
    code, out, err = run_cli(stack_repo, gpush_main, ["--dry-run"], monkeypatch)
    assert code == 0
    assert "refs/for/main" in out
    assert "git push" in out
    assert "About to push commits:" in out
    assert "[dry-run]" in err


def test_gpush_dry_run_does_not_call_input(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: object, **_k: object) -> str:
        raise AssertionError("input should not be called in --dry-run")

    monkeypatch.setattr("builtins.input", _boom)
    code, _out, err = run_cli(stack_repo, gpush_main, ["--dry-run"], monkeypatch)
    assert code == 0
    assert "[dry-run]" in err


def test_gpush_requires_target(stack_repo_unconfigured, monkeypatch):
    repo = stack_repo_unconfigured
    # no configure_gerrit_target
    code, out, err = run_cli(repo, gpush_main, ["--dry-run"], monkeypatch)
    assert code == 1
    assert "Gerrit target" in err or "target" in out.lower()


def test_gpush_dry_run_normalizes_origin_main_to_refs_for_main(
    stack_repo_unconfigured: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """gerritTarget ``origin/main`` must push to ``refs/for/main``, not ``refs/for/origin/main``."""
    repo = stack_repo_unconfigured
    main_sha = git_out("rev-parse", "main", cwd=repo)
    git("update-ref", "refs/remotes/origin/main", main_sha, cwd=repo)
    configure_gerrit_target(repo, "origin/main")
    code, out, err = run_cli(repo, gpush_main, ["--dry-run"], monkeypatch)
    assert code == 0, (out, err)
    assert "refs/for/main" in out
    assert "refs/for/origin/main" not in out


def test_gpush_accepts_explicit_target_without_config(stack_repo_unconfigured, monkeypatch):
    repo = stack_repo_unconfigured
    code, out, _err = run_cli(
        repo,
        gpush_main,
        ["--dry-run", "--target", "main"],
        monkeypatch,
    )
    assert code == 0
    assert "refs/for/main" in out


def test_gpush_fails_on_duplicate_change_ids(dup_repo, monkeypatch):
    code, _out, err = run_cli(dup_repo, gpush_main, ["--dry-run", "--target", "main"], monkeypatch)
    assert code == 2
    assert "Change-Id" in err


class _StdinNonTTY:
    def isatty(self) -> bool:
        return False


class _StdinTTY:
    def isatty(self) -> bool:
        return True


def test_gpush_noninteractive_stdin_requires_yes(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", _StdinNonTTY())
    mock_run = MagicMock()
    monkeypatch.setattr("gerrit_workflow_tools.cli_push._run_git_push", mock_run)
    code, _out, err = run_cli(stack_repo, gpush_main, [], monkeypatch)
    assert code == 1
    assert "non-interactive" in err.lower() or "--yes" in err
    mock_run.assert_not_called()


def test_gpush_noninteractive_yes_runs_push(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", _StdinNonTTY())
    mock_run = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr("gerrit_workflow_tools.cli_push._run_git_push", mock_run)
    code, _out, _err = run_cli(stack_repo, gpush_main, ["--yes"], monkeypatch)
    assert code == 0
    mock_run.assert_called_once()


def test_gpush_cancel_at_prompt(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", _StdinTTY())
    monkeypatch.setattr("builtins.input", lambda _p="": "n")
    mock_run = MagicMock()
    monkeypatch.setattr("gerrit_workflow_tools.cli_push._run_git_push", mock_run)
    code, out, err = run_cli(stack_repo, gpush_main, [], monkeypatch)
    assert code == 0
    assert "About to push commits:" in out
    assert "Stopped at commit" in out
    assert "git push" in out
    assert "cancel" in err.lower()
    mock_run.assert_not_called()


def test_gpush_prompt_preview_order_matches_expected(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", _StdinTTY())
    prompts: list[str] = []

    def _input(prompt: str = "") -> str:
        prompts.append(prompt)
        return "n"

    monkeypatch.setattr("builtins.input", _input)
    mock_run = MagicMock()
    monkeypatch.setattr("gerrit_workflow_tools.cli_push._run_git_push", mock_run)
    code, out, err = run_cli(stack_repo, gpush_main, [], monkeypatch)
    assert code == 0
    i_cmd = out.index("git push")
    i_push = out.index("About to push commits:")
    i_stop = out.index("Stopped at commit")
    i_remain = out.index("not-ready commit(s) remain unpushed")
    assert i_cmd < i_push < i_stop < i_remain
    assert prompts == ["Do you want to push these commits? [Y/n]: "]
    assert "it matches the stop pattern" in out
    assert "git push" in out
    assert "cancel" in err.lower()
    mock_run.assert_not_called()


def test_gpush_reviewers_append_to_refspec(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, _err = run_cli(
        stack_repo,
        gpush_main,
        ["--dry-run", "--reviewers", "alice,bob"],
        monkeypatch,
    )
    assert code == 0
    assert "%r=alice" in out
    assert "%r=bob" in out


def test_gpush_reviewers_merge_config_and_dedupe(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    b = git_out("rev-parse", "--abbrev-ref", "HEAD", cwd=stack_repo)
    set_branch_config(stack_repo, b, gerrit_reviewers="carol")
    code, out, _err = run_cli(
        stack_repo,
        gpush_main,
        ["--dry-run", "--reviewers", "alice", "--reviewers", "alice,bob"],
        monkeypatch,
    )
    assert code == 0
    i = out.index("%r=carol")
    j = out.index("%r=alice")
    k = out.index("%r=bob")
    assert i < j < k


@pytest.mark.parametrize(
    "extra",
    [
        [],
        ["--all"],
        ["--no-config-patterns"],
        ["--ignore-pattern", "^nope$"],
        ["--debug-log"],
    ],
)
def test_gpush_dry_run_variants_exit_zero(stack_repo: Path, monkeypatch: pytest.MonkeyPatch, extra: list[str]) -> None:
    code, out, err = run_cli(stack_repo, gpush_main, ["--dry-run", *extra], monkeypatch)
    assert code == 0, (code, out, err)
    assert "refs/for/main" in out
    assert "git push" in out


def test_gpush_dry_run_highlights_warning_patterns(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = stack_rows_mb_to_head(stack_repo)
    first_subject = rows[0][2]
    git("config", "--unset-all", "gerrit.stopPattern", cwd=stack_repo, check=False)
    git("config", "--add", "gerrit.stopPattern", r"^does-not-match$", cwd=stack_repo)
    git("config", "--unset-all", "gerrit.warningPattern", cwd=stack_repo, check=False)
    git("config", "--add", "gerrit.warningPattern", f"^{re.escape(first_subject)}$", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    code, out, _err = run_cli(stack_repo, gpush_main, ["--dry-run", "--all", "--color", "always"], monkeypatch)
    assert code == 0
    assert ANSI_YELLOW in out
    assert first_subject in out


def test_gpush_dry_run_highlights_stop_boundary_subject(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = stack_rows_mb_to_head(stack_repo)
    assert len(rows) >= 2
    boundary_subject = rows[1][2]
    git("config", "--unset-all", "gerrit.stopPattern", cwd=stack_repo, check=False)
    git("config", "--add", "gerrit.stopPattern", f"^{re.escape(boundary_subject)}$", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    code, out, _err = run_cli(stack_repo, gpush_main, ["--dry-run", "--color", "always"], monkeypatch)
    assert code == 0
    assert "Stopped at commit" in out
    assert ANSI_YELLOW in out
    assert boundary_subject in out


def test_gpush_show_attributes_fails_without_weburl(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, _out, err = run_cli(stack_repo, gpush_main, ["--dry-run", "--show-attributes"], monkeypatch)
    assert code == 1
    assert "gerrit.webUrl" in err or "webUrl" in err


def test_gpush_show_attributes_fails_without_credentials(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "gerrit.webUrl", "https://g.example.test", cwd=stack_repo)
    code, _out, err = run_cli(stack_repo, gpush_main, ["--dry-run", "--show-attributes"], monkeypatch)
    assert code == 1
    assert "credentials" in err.lower()


def test_gpush_show_attributes_unchanged_when_matching_reviewers(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git("config", "gerrit.user", "testuser", cwd=stack_repo)
    git("config", "gerrit.password", "testpass", cwd=stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(
        rows,
        per_index_overrides=[
            {"reviewers": [{"account": {"username": "alice"}, "state": "REVIEWER"}]},
            {"reviewers": [{"account": {"username": "alice"}, "state": "REVIEWER"}]},
            {},
            {},
        ],
    )
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_push", details_by_change_id=details):
        code, out, _err = run_cli(
            stack_repo,
            gpush_main,
            ["--dry-run", "--show-attributes", "--reviewers", "alice"],
            monkeypatch,
        )
    assert code == 0
    assert "About to push commits:" in out
    assert "`r=alice`" in out
    assert "->" not in out


def test_gpush_show_attributes_shows_arrow_when_reviewers_differ(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git("config", "gerrit.user", "testuser", cwd=stack_repo)
    git("config", "gerrit.password", "testpass", cwd=stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(
        rows,
        per_index_overrides=[
            {"reviewers": [{"account": {"username": "alice"}, "state": "REVIEWER"}]},
            {"reviewers": [{"account": {"username": "alice"}, "state": "REVIEWER"}]},
            {},
            {},
        ],
    )
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_push", details_by_change_id=details):
        code, out, _err = run_cli(
            stack_repo,
            gpush_main,
            ["--dry-run", "--show-attributes", "--reviewers", "alice", "--reviewers", "bob"],
            monkeypatch,
        )
    assert code == 0
    assert "->" in out
    assert "`r=alice` -> `r=alice,r=bob`" in out
    assert "%r=alice" in out
    assert "%r=bob" in out


def test_gpush_config_default_show_attributes(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "gerrit.webUrl", "https://g.example.test", cwd=stack_repo)
    git("config", "gerrit.user", "testuser", cwd=stack_repo)
    git("config", "gerrit.password", "testpass", cwd=stack_repo)
    git("config", "gerrit.pushShowAttributes", "true", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(
        rows,
        per_index_overrides=[
            {"reviewers": [{"account": {"username": "alice"}, "state": "REVIEWER"}]},
            {"reviewers": [{"account": {"username": "alice"}, "state": "REVIEWER"}]},
            {},
            {},
        ],
    )
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_push", details_by_change_id=details):
        code, out, _err = run_cli(
            stack_repo,
            gpush_main,
            ["--dry-run", "--reviewers", "alice"],
            monkeypatch,
        )
    assert code == 0
    assert "`r=alice`" in out


def test_gpush_no_show_attributes_overrides_config(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "gerrit.webUrl", "https://g.example.test", cwd=stack_repo)
    git("config", "gerrit.user", "testuser", cwd=stack_repo)
    git("config", "gerrit.password", "testpass", cwd=stack_repo)
    git("config", "gerrit.pushShowAttributes", "true", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(
        rows,
        per_index_overrides=[
            {"reviewers": [{"account": {"username": "alice"}, "state": "REVIEWER"}]},
            {"reviewers": [{"account": {"username": "alice"}, "state": "REVIEWER"}]},
            {},
            {},
        ],
    )
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_push", details_by_change_id=details):
        code, out, _err = run_cli(
            stack_repo,
            gpush_main,
            ["--dry-run", "--no-show-attributes", "--reviewers", "alice"],
            monkeypatch,
        )
    assert code == 0
    assert "`r=alice`" not in out


def test_gpush_show_attributes_wip_no_arrow_when_reviewers_match(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git("config", "gerrit.user", "testuser", cwd=stack_repo)
    git("config", "gerrit.password", "testpass", cwd=stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(
        rows,
        per_index_overrides=[
            {
                "reviewers": [{"account": {"username": "alice"}, "state": "REVIEWER"}],
                "work_in_progress": True,
            },
            {
                "reviewers": [{"account": {"username": "alice"}, "state": "REVIEWER"}],
                "work_in_progress": True,
            },
            {},
            {},
        ],
    )
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_push", details_by_change_id=details):
        code, out, _err = run_cli(
            stack_repo,
            gpush_main,
            ["--dry-run", "--show-attributes", "--reviewers", "alice"],
            monkeypatch,
        )
    assert code == 0
    assert "`r=alice,wip`" in out
    assert "->" not in out


def test_gpush_interactive_reviewers_merges_and_refspec(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", _StdinTTY())
    monkeypatch.setattr("gerrit_workflow_tools.cli_push._prompt_interactive_reviewers", lambda: "bob")
    monkeypatch.setattr("gerrit_workflow_tools.cli_push._prompt_save_reviewers", lambda: False)
    code, out, _err = run_cli(stack_repo, gpush_main, ["--dry-run", "-i"], monkeypatch)
    assert code == 0
    assert "%r=bob" in out


def test_gpush_interactive_reviewers_order_after_branch_and_cli(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    b = git_out("rev-parse", "--abbrev-ref", "HEAD", cwd=stack_repo)
    set_branch_config(stack_repo, b, gerrit_reviewers="carol")
    monkeypatch.setattr(sys, "stdin", _StdinTTY())
    monkeypatch.setattr("gerrit_workflow_tools.cli_push._prompt_interactive_reviewers", lambda: "bob")
    monkeypatch.setattr("gerrit_workflow_tools.cli_push._prompt_save_reviewers", lambda: False)
    code, out, _err = run_cli(
        stack_repo,
        gpush_main,
        ["--dry-run", "-i", "--reviewers", "alice"],
        monkeypatch,
    )
    assert code == 0
    i = out.index("%r=carol")
    j = out.index("%r=alice")
    k = out.index("%r=bob")
    assert i < j < k


def test_gpush_interactive_requires_tty(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", _StdinNonTTY())
    code, _out, err = run_cli(stack_repo, gpush_main, ["--dry-run", "-i"], monkeypatch)
    assert code == 1
    assert "tty" in err.lower()


def test_gpush_interactive_forbidden_with_yes(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", _StdinTTY())
    code, _out, err = run_cli(stack_repo, gpush_main, ["--dry-run", "-i", "--yes"], monkeypatch)
    assert code == 1
    assert "-i" in err


def test_gpush_success_updates_last_push_branch(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "gerrit.lastPushedBranch", "true", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    expected_tip = compute_ready(stack_repo).push_tip_sha
    assert expected_tip
    monkeypatch.setattr(sys, "stdin", _StdinNonTTY())
    mock_run = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr("gerrit_workflow_tools.cli_push._run_git_push", mock_run)
    code, _out, _err = run_cli(stack_repo, gpush_main, ["--yes"], monkeypatch)
    assert code == 0
    assert git_out("rev-parse", "lastPush/feature", cwd=stack_repo) == expected_tip


def test_gpush_skips_last_push_when_config_false(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "gerrit.lastPushedBranch", "false", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    monkeypatch.setattr(sys, "stdin", _StdinNonTTY())
    mock_run = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr("gerrit_workflow_tools.cli_push._run_git_push", mock_run)
    code, _out, _err = run_cli(stack_repo, gpush_main, ["--yes"], monkeypatch)
    assert code == 0
    assert not _ref_exists(stack_repo, "refs/heads/lastPush/feature")


def test_gpush_dry_run_does_not_create_last_push_branch(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "gerrit.lastPushedBranch", "true", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    code, _out, _err = run_cli(stack_repo, gpush_main, ["--dry-run"], monkeypatch)
    assert code == 0
    assert not _ref_exists(stack_repo, "refs/heads/lastPush/feature")


def test_gpush_failed_push_does_not_update_last_push(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "gerrit.lastPushedBranch", "true", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    monkeypatch.setattr(sys, "stdin", _StdinNonTTY())
    mock_run = MagicMock(return_value=MagicMock(returncode=1))
    monkeypatch.setattr("gerrit_workflow_tools.cli_push._run_git_push", mock_run)
    code, _out, _err = run_cli(stack_repo, gpush_main, ["--yes"], monkeypatch)
    assert code == 1
    assert not _ref_exists(stack_repo, "refs/heads/lastPush/feature")


def test_gpush_no_update_last_pushed_overrides_config(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "gerrit.lastPushedBranch", "true", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    monkeypatch.setattr(sys, "stdin", _StdinNonTTY())
    mock_run = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr("gerrit_workflow_tools.cli_push._run_git_push", mock_run)
    code, _out, _err = run_cli(stack_repo, gpush_main, ["--yes", "--no-update-last-pushed"], monkeypatch)
    assert code == 0
    assert not _ref_exists(stack_repo, "refs/heads/lastPush/feature")


def test_gpush_update_last_pushed_flag_enables_when_config_false(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git("config", "gerrit.lastPushedBranch", "false", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    expected_tip = compute_ready(stack_repo).push_tip_sha
    assert expected_tip
    monkeypatch.setattr(sys, "stdin", _StdinNonTTY())
    mock_run = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr("gerrit_workflow_tools.cli_push._run_git_push", mock_run)
    code, _out, _err = run_cli(stack_repo, gpush_main, ["--yes", "--update-last-pushed"], monkeypatch)
    assert code == 0
    assert git_out("rev-parse", "lastPush/feature", cwd=stack_repo) == expected_tip


def _add_self_origin_and_fetch(repo: Path) -> None:
    git("remote", "add", "origin", str(repo.resolve()), cwd=repo)
    git("fetch", "origin", cwd=repo)


def _advance_main_with_commit(repo: Path) -> None:
    git("checkout", "main", cwd=repo)
    (repo / "ahead.txt").write_text("x\n", encoding="utf-8")
    git("add", "ahead.txt", cwd=repo)
    git(
        "commit",
        "-m",
        f"main ahead\n\nChange-Id: I{'a' * 40}",
        cwd=repo,
    )
    git("checkout", "feature", cwd=repo)


def test_gpush_default_remote_policy_skips_rebase_check(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _add_self_origin_and_fetch(stack_repo)
    _advance_main_with_commit(stack_repo)
    clear_gerrit_git_config_cache()
    code, out, err = run_cli(stack_repo, gpush_main, ["--dry-run"], monkeypatch)
    assert code == 0
    assert "refs/for/main" in out
    assert "not based directly" not in err.lower()


def test_gpush_warn_not_rebased_when_remote_ahead(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _add_self_origin_and_fetch(stack_repo)
    _advance_main_with_commit(stack_repo)
    git("config", "gerrit.push.remotePolicy", "warn-not-rebased", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    code, out, err = run_cli(stack_repo, gpush_main, ["--dry-run"], monkeypatch)
    assert code == 0
    assert "refs/for/main" in out
    assert "warning:" in err.lower()
    assert "ger rebase --onto-remote" in err


def test_gpush_error_not_rebased_exits(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _add_self_origin_and_fetch(stack_repo)
    _advance_main_with_commit(stack_repo)
    git("config", "gerrit.push.remotePolicy", "error-not-rebased", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    code, _out, err = run_cli(stack_repo, gpush_main, ["--dry-run"], monkeypatch)
    assert code == 1
    assert "error:" in err.lower()
    assert "ger rebase --onto-remote" in err


def test_gpush_no_rebase_check_bypasses_error_policy(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _add_self_origin_and_fetch(stack_repo)
    _advance_main_with_commit(stack_repo)
    git("config", "gerrit.push.remotePolicy", "error-not-rebased", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    code, out, err = run_cli(stack_repo, gpush_main, ["--dry-run", "--no-rebase-check"], monkeypatch)
    assert code == 0
    assert "refs/for/main" in out
    assert "not based directly" not in err.lower()


def test_gpush_warn_policy_skips_when_fetch_impossible(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "gerrit.push.remotePolicy", "warn-not-rebased", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    code, out, err = run_cli(stack_repo, gpush_main, ["--dry-run"], monkeypatch)
    assert code == 0
    assert "refs/for/main" in out
    assert "skipping remote rebase check" in err.lower()


def test_gpush_cancel_at_prompt_does_not_create_last_push(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "gerrit.lastPushedBranch", "true", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    monkeypatch.setattr(sys, "stdin", _StdinTTY())
    monkeypatch.setattr("builtins.input", lambda _p="": "n")
    mock_run = MagicMock()
    monkeypatch.setattr("gerrit_workflow_tools.cli_push._run_git_push", mock_run)
    code, _out, _err = run_cli(stack_repo, gpush_main, [], monkeypatch)
    assert code == 0
    mock_run.assert_not_called()
    assert not _ref_exists(stack_repo, "refs/heads/lastPush/feature")
