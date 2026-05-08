from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import gerrit_workflow_tools.cli_push as cli_push_mod
from gerrit_workflow_tools.cli_push import main as gpush_main
from gerrit_workflow_tools.cli_style import ANSI_YELLOW
from gerrit_workflow_tools.core.config import clear_gerrit_git_config_cache, set_branch_config
from gerrit_workflow_tools.core.git_run import git, git_out
from gerrit_workflow_tools.core.ready_calc import compute_ready
from gerrit_workflow_tools.core.stack import commits_in_range
from gerrit_workflow_tools.push_input_line import parse as parse_push_options_line
from tests.cli_gerrit_mocks import build_details_by_change_id, patch_gerrit_client_for_queries, stack_rows_mb_to_head
from tests.conftest import run_cli
from tests.fixtures import configure_gerrit_target, make_repo_with_merged_side_branch


def _ref_exists(repo: Path, ref: str) -> bool:
    p = git("rev-parse", "--verify", ref, cwd=repo, check=False)
    return p.returncode == 0


def _mock_gerrit_push_refspec(mock_run: MagicMock) -> str:
    mock_run.assert_called_once()
    cmd, _cwd = mock_run.call_args[0]
    assert cmd[0] == "git" and cmd[1] == "push"
    return cmd[-1]


def test_gpush_help(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, _err = run_cli(stack_repo, gpush_main, ["--help"], monkeypatch, catch_sys_exit=True)
    assert code == 0
    assert "gpush" in out.lower() or "ger push" in out
    assert "--dry-run" in out
    assert "--reviewers" in out
    assert "--reviewer-strategy" in out
    scrubbed = out.replace("--reviewers", "").replace("--reviewer-strategy", "")
    assert "--reviewer" not in scrubbed
    assert "--ignore-pattern" in out
    assert "--update-last-pushed" in out
    assert "--no-update-last-pushed" in out
    assert "--no-rebase-check" in out
    assert "-i" in out
    assert "--follow-merges" in out


def test_gpush_dry_run_prints_commit_preview(stack_repo, monkeypatch):
    """Gerrit dry-run shows the stack preview only; refspec is not echoed to stdout."""
    code, out, err = run_cli(stack_repo, gpush_main, ["--dry-run"], monkeypatch)
    assert code == 0
    assert "About to push commits:" in out
    assert "git push" not in out
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
    # no configure_gerrit_target, no upstream → no push destination
    code, _out, err = run_cli(repo, gpush_main, ["--dry-run"], monkeypatch)
    assert code == 1
    assert "push destination" in err.lower() or "gerritTarget" in err.lower()


def test_gpush_prompts_for_missing_upstream_and_aborts(
    stack_repo_unconfigured: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = stack_repo_unconfigured
    git("branch", "--unset-upstream", "feature", cwd=repo, check=False)
    set_branch_config(repo, "feature", gerrit_target="main")
    monkeypatch.setattr(sys, "stdin", _StdinTTY())
    monkeypatch.setattr(
        "gerrit_workflow_tools.core.upstream_interactive.prompt_upstream_abbrev_interactive",
        lambda _cwd, _branch: None,
    )
    code, _out, _err = run_cli(repo, gpush_main, ["--dry-run"], monkeypatch)
    assert code == 1
    p = git("rev-parse", "--abbrev-ref", "feature@{upstream}", cwd=repo, check=False)
    assert p.returncode != 0


def test_gpush_sets_missing_upstream_then_continues(
    stack_repo_unconfigured: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = stack_repo_unconfigured
    git("branch", "--unset-upstream", "feature", cwd=repo, check=False)
    set_branch_config(repo, "feature", gerrit_target="main")
    monkeypatch.setattr(sys, "stdin", _StdinTTY())
    monkeypatch.setattr(
        "gerrit_workflow_tools.core.upstream_interactive.prompt_upstream_abbrev_interactive",
        lambda _cwd, _branch: "main",
    )
    code, _out, err = run_cli(repo, gpush_main, ["--dry-run"], monkeypatch)
    assert code == 0
    assert "Upstream for 'feature' set to main." in err
    assert git_out("rev-parse", "--abbrev-ref", "feature@{upstream}", cwd=repo) == "main"


def test_gpush_vanilla_upstream_runs_plain_git_push(
    stack_repo_unconfigured: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Upstream on a remote other than gerrit.remote → ``git push`` with no extra args (B1)."""
    repo = stack_repo_unconfigured
    git("remote", "add", "origin", str(repo.resolve()), cwd=repo)
    git("remote", "add", "fork", str(repo.resolve()), cwd=repo)
    git("fetch", "fork", cwd=repo)
    git("branch", "--set-upstream-to=fork/main", "feature", cwd=repo)
    clear_gerrit_git_config_cache()
    mock_run = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr("gerrit_workflow_tools.cli_push._run_git_push", mock_run)
    monkeypatch.setattr(sys, "stdin", _StdinNonTTY())
    code, _out, _err = run_cli(repo, gpush_main, ["--yes"], monkeypatch)
    assert code == 0
    mock_run.assert_called_once_with(["git", "push"], repo)


def test_gpush_detached_head_errors(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("checkout", "--detach", "HEAD", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    code, _out, err = run_cli(stack_repo, gpush_main, ["--dry-run"], monkeypatch)
    assert code == 1
    assert "detached" in err.lower()


def test_gpush_infers_gerrit_target_from_upstream(
    stack_repo_unconfigured: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No gerritTarget: upstream on gerrit.remote implies Gerrit push to refs/for/<branch>."""
    repo = stack_repo_unconfigured
    _add_self_origin_and_fetch(repo)
    git("branch", "--set-upstream-to=origin/main", "feature", cwd=repo)
    clear_gerrit_git_config_cache()
    mock_run = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr("gerrit_workflow_tools.cli_push._run_git_push", mock_run)
    monkeypatch.setattr(sys, "stdin", _StdinNonTTY())
    code, _out, _err = run_cli(repo, gpush_main, ["--yes"], monkeypatch)
    assert code == 0
    assert ":refs/for/main" in _mock_gerrit_push_refspec(mock_run)


def test_gpush_dry_run_normalizes_origin_main_to_refs_for_main(
    stack_repo_unconfigured: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """gerritTarget ``origin/main`` must push to ``refs/for/main``, not ``refs/for/origin/main``."""
    repo = stack_repo_unconfigured
    main_sha = git_out("rev-parse", "main", cwd=repo)
    git("update-ref", "refs/remotes/origin/main", main_sha, cwd=repo)
    configure_gerrit_target(repo, "origin/main")
    mock_run = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr("gerrit_workflow_tools.cli_push._run_git_push", mock_run)
    monkeypatch.setattr(sys, "stdin", _StdinNonTTY())
    code, _out, _err = run_cli(repo, gpush_main, ["--yes"], monkeypatch)
    assert code == 0
    refspec = _mock_gerrit_push_refspec(mock_run)
    assert ":refs/for/main" in refspec
    assert "origin/main" not in refspec


def test_gpush_fails_on_duplicate_change_ids(dup_repo, monkeypatch):
    code, _out, err = run_cli(dup_repo, gpush_main, ["--dry-run"], monkeypatch)
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
    assert "git push" not in out
    i_push = out.index("About to push commits:")
    i_stop = out.index("Stopped at commit")
    i_remain = out.index("not-ready commit(s) remain unpushed")
    i_status = out.index("Branch", i_remain)
    assert i_push < i_stop < i_remain < i_status
    assert "Target" in out[i_status:]
    assert "feature" in out[i_status:]
    assert "main" in out[i_status:]
    assert "Reviewers" in out[i_status:]
    assert "(none)" in out[i_status:]
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
    i_push = out.index("About to push commits:")
    i_stop = out.index("Stopped at commit")
    i_remain = out.index("not-ready commit(s) remain unpushed")
    i_branch = out.index("Branch", i_remain)
    assert i_push < i_stop < i_remain < i_branch
    assert prompts == ["Do you want to push these commits? [Y/n/r]: "]
    assert "it matches the stop pattern" in out
    assert "git push" not in out
    assert "cancel" in err.lower()
    mock_run.assert_not_called()


def test_gpush_reviewers_append_to_refspec(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mock_run = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr("gerrit_workflow_tools.cli_push._run_git_push", mock_run)
    monkeypatch.setattr(sys, "stdin", _StdinNonTTY())
    code, _out, _err = run_cli(
        stack_repo,
        gpush_main,
        ["--yes", "--reviewers", "alice,bob"],
        monkeypatch,
    )
    assert code == 0
    refspec = _mock_gerrit_push_refspec(mock_run)
    assert "%r=alice,r=bob" in refspec


def test_gpush_lazy_strategy_omits_refspec_percent_r(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, err = run_cli(
        stack_repo,
        gpush_main,
        ["--dry-run", "--reviewers", "alice", "--reviewer-strategy", "lazy"],
        monkeypatch,
    )
    assert code == 0
    assert "%r=" not in out
    assert "%r=" not in err
    assert "lazy" in err and "reviewers" in err


def test_gpush_yes_lazy_without_rest_credentials_errors(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", _StdinNonTTY())
    code, _out, err = run_cli(
        stack_repo,
        gpush_main,
        ["--yes", "--reviewer-strategy", "lazy", "--reviewers", "alice"],
        monkeypatch,
    )
    assert code == 1
    assert "REST" in err or "gerrit" in err.lower()


def test_gpush_yes_overwrite_prints_per_commit_assignment_lines(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git("config", "gerrit.webUrl", "https://g.example.test", cwd=stack_repo)
    git("config", "gerrit.user", "testuser", cwd=stack_repo)
    git("config", "gerrit.password", "testpass", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    ready = compute_ready(stack_repo)
    assert ready.push_range
    rows = commits_in_range(stack_repo, ready.push_range, first_parent=True)
    reviewer_existing = {
        "reviewers": [{"account": {"username": "bob", "_account_id": 3}, "state": "REVIEWER"}],
    }
    details = build_details_by_change_id(rows, per_index_overrides=[reviewer_existing] * len(rows))
    mock_run = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr("gerrit_workflow_tools.cli_push._run_git_push", mock_run)
    monkeypatch.setattr(sys, "stdin", _StdinNonTTY())
    with patch_gerrit_client_for_queries("gerrit_workflow_tools.cli_push", details_by_change_id=details):
        code, out, _err = run_cli(
            stack_repo,
            gpush_main,
            ["--yes", "--reviewer-strategy", "overwrite", "--reviewers", "alice,ben"],
            monkeypatch,
        )
    assert code == 0
    assignment_lines = [ln for ln in out.splitlines() if " assigned " in ln]
    assert len(assignment_lines) == len(rows)
    for row in rows:
        assert row.subject in out
        assert row.sha in out
    assert "r=alice" in out and "r=ben" in out


def test_gpush_reviewers_cli_overwrites_branch_and_dedupes(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    b = git_out("rev-parse", "--abbrev-ref", "HEAD", cwd=stack_repo)
    set_branch_config(stack_repo, b, gerrit_reviewers="carol")
    mock_run = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr("gerrit_workflow_tools.cli_push._run_git_push", mock_run)
    monkeypatch.setattr(sys, "stdin", _StdinNonTTY())
    code, _out, _err = run_cli(
        stack_repo,
        gpush_main,
        ["--yes", "--reviewers", "alice", "--reviewers", "alice,bob"],
        monkeypatch,
    )
    assert code == 0
    refspec = _mock_gerrit_push_refspec(mock_run)
    assert "carol" not in refspec
    i = refspec.index("r=alice")
    j = refspec.index("r=bob")
    assert i < j


@pytest.mark.parametrize(
    "extra",
    [
        [],
        ["--all"],
        ["--ignore-pattern", "^nope$"],
        ["--debug-log"],
    ],
)
def test_gpush_dry_run_variants_exit_zero(stack_repo: Path, monkeypatch: pytest.MonkeyPatch, extra: list[str]) -> None:
    code, out, err = run_cli(stack_repo, gpush_main, ["--dry-run", *extra], monkeypatch)
    assert code == 0, (code, out, err)
    assert "About to push commits:" in out
    assert "git push" not in out
    assert "[dry-run]" in err
    if "--all" in extra:
        assert "Cleanup after experiment" in out


def test_gpush_dry_run_highlights_warning_patterns(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = stack_rows_mb_to_head(stack_repo)
    first_subject = rows[0].subject
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
    boundary_subject = rows[1].subject
    git("config", "--unset-all", "gerrit.stopPattern", cwd=stack_repo, check=False)
    git("config", "--add", "gerrit.stopPattern", f"^{re.escape(boundary_subject)}$", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    code, out, _err = run_cli(stack_repo, gpush_main, ["--dry-run", "--color", "always"], monkeypatch)
    assert code == 0
    assert "Stopped at commit" in out
    assert ANSI_YELLOW in out
    assert boundary_subject in out


def test_gpush_show_attributes_fails_without_weburl(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "gerrit.pushShowAttributes", "true", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    code, _out, err = run_cli(stack_repo, gpush_main, ["--dry-run"], monkeypatch)
    assert code == 1
    assert "gerrit.webUrl" in err or "webUrl" in err


def test_gpush_show_attributes_fails_without_credentials(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "gerrit.webUrl", "https://g.example.test", cwd=stack_repo)
    git("config", "gerrit.pushShowAttributes", "true", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    code, _out, err = run_cli(stack_repo, gpush_main, ["--dry-run"], monkeypatch)
    assert code == 1
    assert "credentials" in err.lower()


def test_gpush_show_attributes_unchanged_when_matching_reviewers(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    assert "About to push commits:" in out
    assert "`r=alice`" in out
    assert "->" not in out


def test_gpush_show_attributes_shows_arrow_when_reviewers_differ(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
            ["--dry-run", "--reviewers", "alice", "--reviewers", "bob"],
            monkeypatch,
        )
    assert code == 0
    assert "->" in out
    assert "`r=alice` -> `r=alice,r=bob`" in out


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


def test_gpush_push_show_attributes_false_skips_attribute_suffix(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git("config", "gerrit.webUrl", "https://g.example.test", cwd=stack_repo)
    git("config", "gerrit.user", "testuser", cwd=stack_repo)
    git("config", "gerrit.password", "testpass", cwd=stack_repo)
    git("config", "gerrit.pushShowAttributes", "false", cwd=stack_repo)
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
    assert "`r=alice`" not in out


def test_gpush_show_attributes_wip_no_arrow_when_reviewers_match(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git("config", "gerrit.webUrl", "https://g.example.test", cwd=stack_repo)
    git("config", "gerrit.user", "testuser", cwd=stack_repo)
    git("config", "gerrit.password", "testpass", cwd=stack_repo)
    git("config", "gerrit.pushShowAttributes", "true", cwd=stack_repo)
    clear_gerrit_git_config_cache()
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
            ["--dry-run", "--reviewers", "alice"],
            monkeypatch,
        )
    assert code == 0
    assert "`r=alice,wip`" in out
    assert "->" not in out


def test_gpush_interactive_reviewers_refspec(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    refs: list[str] = []
    orig = cli_push_mod._refs_for_spec

    def _capture(tip: str, push_branch: str, state: object, strategy: object) -> str:
        r = orig(tip, push_branch, state, strategy)
        refs.append(r)
        return r

    monkeypatch.setattr(cli_push_mod, "_refs_for_spec", _capture)
    monkeypatch.setattr(sys, "stdin", _StdinTTY())
    monkeypatch.setattr(
        "gerrit_workflow_tools.cli_push._prompt_interactive_reviewers",
        lambda *_a, **_k: parse_push_options_line("bob"),
    )
    monkeypatch.setattr("gerrit_workflow_tools.cli_push._prompt_save_reviewers", lambda: False)
    code, _out, _err = run_cli(stack_repo, gpush_main, ["--dry-run", "-i"], monkeypatch)
    assert code == 0
    assert refs and "%r=bob" in refs[-1]


def test_gpush_interactive_reviewers_lazy_omits_percent_r(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    refs: list[str] = []
    orig = cli_push_mod._refs_for_spec

    def _capture(tip: str, push_branch: str, state: object, strategy: object) -> str:
        r = orig(tip, push_branch, state, strategy)
        refs.append(r)
        return r

    monkeypatch.setattr(cli_push_mod, "_refs_for_spec", _capture)
    monkeypatch.setattr(sys, "stdin", _StdinTTY())
    monkeypatch.setattr(
        "gerrit_workflow_tools.cli_push._prompt_interactive_reviewers",
        lambda *_a, **_k: parse_push_options_line("bob lazy"),
    )
    monkeypatch.setattr("gerrit_workflow_tools.cli_push._prompt_save_reviewers", lambda: False)
    code, _out, err = run_cli(stack_repo, gpush_main, ["--dry-run", "-i"], monkeypatch)
    assert code == 0
    assert "lazy" in err
    assert refs and "%r=bob" not in refs[-1]


def test_gpush_confirm_reviewers_then_push_includes_percent_r(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After ``r`` at confirm, inline options drive push without a second strategy prompt."""
    monkeypatch.setattr(sys, "stdin", _StdinTTY())
    _answers = iter(["r", ""])  # reviewers path, then confirm push

    def _input(prompt: str = "") -> str:
        return next(_answers)

    monkeypatch.setattr("builtins.input", _input)
    monkeypatch.setattr(
        "gerrit_workflow_tools.cli_push._prompt_reviewers_line_ptk",
        lambda *_a, **_k: parse_push_options_line("alice"),
    )
    mock_run = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr("gerrit_workflow_tools.cli_push._run_git_push", mock_run)
    code, _out, _err = run_cli(stack_repo, gpush_main, [], monkeypatch)
    assert code == 0
    refspec = _mock_gerrit_push_refspec(mock_run)
    assert "%r=alice" in refspec


def test_gpush_interactive_reviewers_overwrites_branch_and_cli(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    refs: list[str] = []
    orig = cli_push_mod._refs_for_spec

    def _capture(tip: str, push_branch: str, state: object, strategy: object) -> str:
        r = orig(tip, push_branch, state, strategy)
        refs.append(r)
        return r

    b = git_out("rev-parse", "--abbrev-ref", "HEAD", cwd=stack_repo)
    set_branch_config(stack_repo, b, gerrit_reviewers="carol")
    monkeypatch.setattr(cli_push_mod, "_refs_for_spec", _capture)
    monkeypatch.setattr(sys, "stdin", _StdinTTY())
    monkeypatch.setattr(
        "gerrit_workflow_tools.cli_push._prompt_interactive_reviewers",
        lambda *_a, **_k: parse_push_options_line("bob"),
    )
    monkeypatch.setattr("gerrit_workflow_tools.cli_push._prompt_save_reviewers", lambda: False)
    code, _out, _err = run_cli(
        stack_repo,
        gpush_main,
        ["--dry-run", "-i", "--reviewers", "alice"],
        monkeypatch,
    )
    assert code == 0
    refspec = refs[-1]
    assert "carol" not in refspec
    assert "alice" not in refspec
    assert "%r=bob" in refspec


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


def _make_merge_branch_repo(tmp_path: Path) -> Path:
    """Thin wrapper around the shared fixture helper (see ``fixtures.make_repo_with_merged_side_branch``)."""
    return make_repo_with_merged_side_branch(tmp_path / "r")


def test_compute_ready_with_merged_side_branch_counts_only_first_parent_commits(
    tmp_path: Path,
) -> None:
    """
    Regression: merging a side branch must not bloat the push commit list.

    The push range should contain only the first-parent commits on the feature
    branch (local work + merge commit = 2), not the side-branch commits (S1,
    S2) that are reachable via the merge commit's second parent.
    """
    repo = _make_merge_branch_repo(tmp_path)
    result = compute_ready(repo, all_commits=True)
    # Currently FAILS: without --first-parent, git log also traverses the
    # second parent of the merge commit, yielding 4 commits (local + S1 + S2 +
    # merge) instead of the correct 2 (local + merge).
    assert result.pushable_count == 2, (
        f"expected 2 first-parent commits (local work + merge), got {result.pushable_count}"
    )


def test_gpush_dry_run_with_merged_side_branch_lists_only_first_parent_commits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Regression: the push preview must not list side-branch commits.

    Only the two first-parent commits (local work + merge commit) should
    appear in the 'About to push commits:' output.
    """
    repo = _make_merge_branch_repo(tmp_path)
    code, out, err = run_cli(repo, gpush_main, ["--dry-run", "--all"], monkeypatch)
    assert code == 0, (out, err)
    assert "local work" in out
    assert "Merge side branch" in out
    assert "side commit 1" not in out
    assert "side commit 2" not in out


def test_compute_ready_follow_merges_restores_all_parents_count(tmp_path: Path) -> None:
    """
    ``--follow-merges`` (first_parent=False) must restore the full-DAG count.

    With ``first_parent=False``, ``compute_ready`` traverses both parents of the
    merge commit and returns 4 commits (local + S1 + S2 + merge-M).
    """
    repo = _make_merge_branch_repo(tmp_path)
    result = compute_ready(repo, all_commits=True, first_parent=False)
    assert result.pushable_count == 4, f"expected 4 commits with full-DAG traversal, got {result.pushable_count}"


def test_gpush_follow_merges_flag_lists_side_branch_commits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--follow-merges`` must re-include side-branch commits in the push preview."""
    repo = _make_merge_branch_repo(tmp_path)
    code, out, err = run_cli(repo, gpush_main, ["--dry-run", "--all", "--follow-merges"], monkeypatch)
    assert code == 0, (out, err)
    assert "side commit 1" in out
    assert "side commit 2" in out


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
    assert "About to push commits:" in out
    assert "not based directly" not in err.lower()


def test_gpush_warn_not_rebased_when_remote_ahead(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _add_self_origin_and_fetch(stack_repo)
    _advance_main_with_commit(stack_repo)
    git("config", "gerrit.push.remotePolicy", "warn-not-rebased", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    code, out, err = run_cli(stack_repo, gpush_main, ["--dry-run"], monkeypatch)
    assert code == 0
    assert "About to push commits:" in out
    assert "warning:" in err.lower()
    assert "ger restack --onto-remote" in err


def test_gpush_error_not_rebased_exits(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _add_self_origin_and_fetch(stack_repo)
    _advance_main_with_commit(stack_repo)
    git("config", "gerrit.push.remotePolicy", "error-not-rebased", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    code, _out, err = run_cli(stack_repo, gpush_main, ["--dry-run"], monkeypatch)
    assert code == 1
    assert "error:" in err.lower()
    assert "ger restack --onto-remote" in err


def test_gpush_no_rebase_check_bypasses_error_policy(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _add_self_origin_and_fetch(stack_repo)
    _advance_main_with_commit(stack_repo)
    git("config", "gerrit.push.remotePolicy", "error-not-rebased", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    code, out, err = run_cli(stack_repo, gpush_main, ["--dry-run", "--no-rebase-check"], monkeypatch)
    assert code == 0
    assert "About to push commits:" in out
    assert "not based directly" not in err.lower()


def test_gpush_warn_policy_skips_when_fetch_impossible(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "gerrit.push.remotePolicy", "warn-not-rebased", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    code, out, err = run_cli(stack_repo, gpush_main, ["--dry-run"], monkeypatch)
    assert code == 0
    assert "About to push commits:" in out
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
