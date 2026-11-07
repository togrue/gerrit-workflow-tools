# Spec: docu/spec/commands/push.md
# Covers: --dry-run, --yes, duplicate Change-Id, lastPush branch, merged side branch, reviewer strategies

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_log import main as log_main
from gerrit_workflow_tools.cli_push import main as gpush_main
from gerrit_workflow_tools.cli_style import ANSI_YELLOW
from gerrit_workflow_tools.core.config_write import set_branch_config
from gerrit_workflow_tools.core.gerrit.rest import GerritApiError
from gerrit_workflow_tools.core.git_run import git, git_out
from gerrit_workflow_tools.core.ready_calc import compute_ready
from gerrit_workflow_tools.core.stack import commits_in_range
from gerrit_workflow_tools.push_input_line import parse as parse_push_options_line
from gerrit_workflow_tools.push_options_history import load_push_options_history
from tests.change_store import ChangeStore
from tests.cli_gerrit_mocks import build_details_by_change_id, stack_rows_mb_to_head
from tests.conftest import run_cli
from tests.fixtures import make_repo_with_merged_side_branch
from tests.helpers import ref_exists, write_rebase_head


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
    assert "--branch" in out
    assert "--reviewers" in out
    assert "--reviewer-strategy" in out
    assert "--topic" in out
    assert "--wip" in out
    assert "--private" in out
    scrubbed = out.replace("--reviewers", "").replace("--reviewer-strategy", "")
    assert "--reviewer" not in scrubbed
    assert "--no-rebase-check" in out
    assert "-i" in out
    assert "--follow-merges" in out


def test_gpush_dry_run_prints_commit_preview(stack_repo, monkeypatch):
    """Gerrit dry-run shows the stack preview and the would-be ``git push`` refspec."""
    code, out, err = run_cli(stack_repo, gpush_main, ["--dry-run"], monkeypatch)
    assert code == 0
    assert "About to push commits:" in out
    assert "git push" in out
    assert ":refs/for/" in out
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
    assert code == ExitCode.GIT
    assert "push destination" in err.lower() or "upstream" in err.lower()


def test_gpush_prompts_for_missing_upstream_and_aborts(
    stack_repo_unconfigured: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = stack_repo_unconfigured
    git("branch", "--unset-upstream", "feature", cwd=repo, check=False)
    monkeypatch.setattr(sys, "stdin", _StdinTTY())
    monkeypatch.setattr(
        "gerrit_workflow_tools.core.upstream_interactive.prompt_upstream_abbrev_interactive",
        lambda _cwd, _branch, **_kw: None,
    )
    code, _out, _err = run_cli(repo, gpush_main, [], monkeypatch)
    assert code == 1
    p = git("rev-parse", "--abbrev-ref", "feature@{upstream}", cwd=repo, check=False)
    assert p.returncode != 0


def test_gpush_sets_missing_upstream_then_continues(
    stack_repo_unconfigured: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = stack_repo_unconfigured
    _add_self_origin_and_fetch(repo)
    git("branch", "--unset-upstream", "feature", cwd=repo, check=False)
    monkeypatch.setattr(sys, "stdin", _StdinTTY())
    monkeypatch.setattr(
        "gerrit_workflow_tools.core.upstream_interactive.prompt_upstream_abbrev_interactive",
        lambda _cwd, _branch, **_kw: "origin/main",
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    code, _out, err = run_cli(repo, gpush_main, [], monkeypatch)
    assert code == 0
    assert "Upstream for 'feature' set to origin/main." in err
    assert git_out("rev-parse", "--abbrev-ref", "feature@{upstream}", cwd=repo) == "origin/main"


def test_gpush_dry_run_never_writes_upstream(stack_repo_unconfigured: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = stack_repo_unconfigured
    _add_self_origin_and_fetch(repo)
    git("branch", "--unset-upstream", "feature", cwd=repo, check=False)
    monkeypatch.setattr(sys, "stdin", _StdinTTY())
    monkeypatch.setattr(
        "gerrit_workflow_tools.core.upstream_interactive.prompt_upstream_abbrev_interactive",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("--dry-run must not prompt for an upstream")),
    )
    code, _out, err = run_cli(repo, gpush_main, ["--dry-run"], monkeypatch)
    assert code == ExitCode.GIT
    assert "push destination" in err.lower()
    assert git("rev-parse", "--abbrev-ref", "feature@{upstream}", cwd=repo, check=False).returncode != 0


def test_gpush_vanilla_upstream_runs_plain_git_push(
    stack_repo_unconfigured: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Upstream on a remote other than gerrit.remote → ``git push`` with no extra args (B1)."""
    repo = stack_repo_unconfigured
    git("remote", "add", "origin", str(repo.resolve()), cwd=repo)
    git("remote", "add", "fork", str(repo.resolve()), cwd=repo)
    git("fetch", "fork", cwd=repo)
    git("branch", "--set-upstream-to=fork/main", "feature", cwd=repo)
    mock_run = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr("gerrit_workflow_tools.core.push.run_git_push", mock_run)
    monkeypatch.setattr(sys, "stdin", _StdinNonTTY())
    code, _out, _err = run_cli(repo, gpush_main, ["--yes"], monkeypatch)
    assert code == 0
    mock_run.assert_called_once_with(["git", "push"], repo)


def test_gpush_detached_head_no_local_branch_errors(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("checkout", "--detach", "HEAD", cwd=stack_repo)
    (stack_repo / "detached-only.txt").write_text("x\n", encoding="utf-8")
    git("add", "detached-only.txt", cwd=stack_repo)
    git("commit", "-m", "detached-only", cwd=stack_repo)
    code, _out, err = run_cli(stack_repo, gpush_main, ["--dry-run"], monkeypatch)
    assert code == ExitCode.GIT
    assert "detached" in err.lower()


def test_gpush_detached_head_uses_rebase_branch(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("checkout", "--detach", "HEAD", cwd=stack_repo)
    (stack_repo / "rebased.txt").write_text("x\n", encoding="utf-8")
    git("add", "rebased.txt", cwd=stack_repo)
    git(
        "commit",
        "-m",
        "rebased detached commit",
        "-m",
        "Change-Id: I00000000000000000000000000000000000000aa",
        cwd=stack_repo,
    )
    write_rebase_head(stack_repo, "feature")

    code, out, err = run_cli(stack_repo, gpush_main, ["--dry-run"], monkeypatch)

    assert code == 0, err
    assert ":refs/for/main" in out
    assert "warning: a rebase is in progress" in err


def test_gpush_detached_head_does_not_prompt_upstream(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("checkout", "--detach", "HEAD", cwd=stack_repo)
    monkeypatch.setattr(sys, "stdin", _StdinTTY())
    monkeypatch.setattr(
        "gerrit_workflow_tools.core.upstream_interactive.prompt_upstream_abbrev_interactive",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("upstream prompt on detached HEAD")),
    )
    code, _out, _err = run_cli(stack_repo, gpush_main, ["--dry-run"], monkeypatch)
    assert code == 0


def test_gpush_branch_flag_pushes_specified_branch(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("checkout", "main", cwd=stack_repo)
    code, out, err = run_cli(stack_repo, gpush_main, ["--branch", "feature", "--dry-run"], monkeypatch)
    assert code == 0
    assert "About to push commits:" in out
    push_lines = [ln for ln in out.splitlines() if ln.startswith("    ")]
    assert len(push_lines) == 2
    assert any("Refactor parser init" in ln for ln in push_lines)
    assert any("Extract command routing" in ln for ln in push_lines)
    assert "Stopped at commit" in out
    assert ":refs/for/main" in out
    assert "[dry-run]" in err


def test_gpush_branch_flag_unknown_branch_errors(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, _out, err = run_cli(stack_repo, gpush_main, ["--branch", "no-such-branch", "--dry-run"], monkeypatch)
    assert code == ExitCode.NOT_FOUND
    assert "no-such-branch" in err


def test_gpush_dry_run_normalizes_origin_main_to_refs_for_main(
    stack_repo_unconfigured: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Upstream ``origin/main`` must push to ``refs/for/main``, not ``refs/for/origin/main``."""
    repo = stack_repo_unconfigured
    _add_self_origin_and_fetch(repo)
    git("branch", "--set-upstream-to=origin/main", "feature", cwd=repo)
    mock_run = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr("gerrit_workflow_tools.core.push.run_git_push", mock_run)
    monkeypatch.setattr(sys, "stdin", _StdinNonTTY())
    code, _out, _err = run_cli(repo, gpush_main, ["--yes"], monkeypatch)
    assert code == 0
    refspec = _mock_gerrit_push_refspec(mock_run)
    assert ":refs/for/main" in refspec
    assert "origin/main" not in refspec


def test_gpush_duplicate_change_id_stops_at_boundary(dup_repo, monkeypatch):
    """Duplicate Change-Id soft-fails: push the good prefix, show boundary reason."""
    code, out, err = run_cli(dup_repo, gpush_main, ["--dry-run"], monkeypatch)
    assert code == 0
    assert "About to push commits:" in out
    assert "Stopped at commit" in out
    assert "duplicate Change-Id" in out
    about, _stopped = out.split("Stopped at commit", 1)
    assert "dup 0" in about
    assert "dup 1" not in about
    assert about.count(" # ") == 1
    assert "[dry-run]" in err


class _StdinNonTTY:
    def isatty(self) -> bool:
        return False


class _StdinTTY:
    def isatty(self) -> bool:
        return True


def test_gpush_treats_windows_nul_as_non_interactive(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """On Windows ``isatty()`` is true for ``NUL``, which is what ``</dev/null`` opens."""
    monkeypatch.setattr(sys, "stdin", _StdinTTY())
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr("gerrit_workflow_tools.core.upstream_interactive._is_windows_console", lambda _stream: False)
    monkeypatch.setattr("builtins.input", lambda _prompt="": pytest.fail("must not prompt"))

    code, _out, err = run_cli(stack_repo, gpush_main, [], monkeypatch)

    assert code == 1
    assert "--yes" in err


def test_gpush_i_treats_windows_nul_as_non_interactive(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", _StdinTTY())
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr("gerrit_workflow_tools.core.upstream_interactive._is_windows_console", lambda _stream: False)

    code, _out, err = run_cli(stack_repo, gpush_main, ["--dry-run", "-i"], monkeypatch)

    assert code == 1
    assert "-i requires an interactive terminal" in err


@pytest.mark.parametrize("interrupt", [EOFError, KeyboardInterrupt])
def test_gpush_end_of_input_at_the_prompt_cancels(
    interrupt: type[BaseException], stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _input(_prompt: str = "") -> str:
        raise interrupt

    monkeypatch.setattr(sys, "stdin", _StdinTTY())
    monkeypatch.setattr("builtins.input", _input)
    mock_run = MagicMock()
    monkeypatch.setattr("gerrit_workflow_tools.core.push.run_git_push", mock_run)

    code, _out, err = run_cli(stack_repo, gpush_main, [], monkeypatch)

    assert code == 0
    assert "Push cancelled." in err
    mock_run.assert_not_called()


def test_gpush_noninteractive_stdin_requires_yes(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", _StdinNonTTY())
    mock_run = MagicMock()
    monkeypatch.setattr("gerrit_workflow_tools.core.push.run_git_push", mock_run)
    code, _out, err = run_cli(stack_repo, gpush_main, [], monkeypatch)
    assert code == 1
    assert "non-interactive" in err.lower() or "--yes" in err
    mock_run.assert_not_called()


def test_gpush_noninteractive_yes_runs_push(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", _StdinNonTTY())
    mock_run = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr("gerrit_workflow_tools.core.push.run_git_push", mock_run)
    code, _out, _err = run_cli(stack_repo, gpush_main, ["--yes"], monkeypatch)
    assert code == 0
    mock_run.assert_called_once()


def test_gpush_cancel_at_prompt(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "gerrit.lastPushedBranch", "true", cwd=stack_repo)
    monkeypatch.setattr(sys, "stdin", _StdinTTY())
    prompts: list[str] = []

    def _input(prompt: str = "") -> str:
        prompts.append(prompt)
        return "n"

    monkeypatch.setattr("builtins.input", _input)
    mock_run = MagicMock()
    monkeypatch.setattr("gerrit_workflow_tools.core.push.run_git_push", mock_run)
    code, out, err = run_cli(stack_repo, gpush_main, [], monkeypatch)
    assert code == 0
    assert "About to push commits:" in out
    assert "Stopped at commit" in out
    assert "git push" not in out
    i_push = out.index("About to push commits:")
    i_stop = out.index("Stopped at commit")
    i_remain = out.index("2 not-ready commit(s) remain unpushed")
    i_status = out.index("Branch", i_remain)
    assert i_push < i_stop < i_remain < i_status
    assert "Target" in out[i_status:]
    assert "feature" in out[i_status:]
    assert "main" in out[i_status:]
    assert "Reviewers" in out[i_status:]
    assert "(none)" in out[i_status:]
    assert "Topic" in out[i_status:]
    assert "WIP" in out[i_status:]
    assert "Private" in out[i_status:]
    assert "no" in out[i_status:]
    assert "it matches the stop pattern" in out
    assert prompts == ["Do you want to push these commits? [Y/n/r]: "]
    assert "cancel" in err.lower()
    mock_run.assert_not_called()
    assert not ref_exists(stack_repo, "refs/heads/lastPush/feature")


def test_gpush_reviewers_append_to_refspec(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mock_run = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr("gerrit_workflow_tools.core.push.run_git_push", mock_run)
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


def test_gpush_lazy_dry_run_includes_topic_and_wip_on_refspec(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, out, _err = run_cli(
        stack_repo,
        gpush_main,
        [
            "--dry-run",
            "--reviewers",
            "alice",
            "--reviewer-strategy",
            "lazy",
            "--topic",
            "my-series",
            "--wip",
        ],
        monkeypatch,
    )
    assert code == 0
    assert "%r=" not in out
    line = next(ln for ln in out.splitlines() if ln.strip().startswith("git push"))
    assert "wip" in line
    assert "topic=my-series" in line


def test_gpush_dry_run_status_line_spells_out_topic_wip_and_private(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, out, _err = run_cli(
        stack_repo,
        gpush_main,
        ["--dry-run", "--reviewers", "alice", "--topic", "my-series", "--wip", "--private"],
        monkeypatch,
    )
    assert code == 0
    i_status = out.index("Branch")
    status = out[i_status:]
    assert "Target" in status
    assert "Reviewers" in status
    assert "Topic" in status
    assert "my-series" in status
    assert "WIP" in status
    assert "Private" in status
    assert "yes" in status


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


def test_git_push_rejected_no_new_changes_detects_gerrit_message() -> None:
    from gerrit_workflow_tools.core.push import push_rejected_no_new_changes

    rejected = "! [remote rejected] deadbeef -> refs/for/main (no new changes)\n"
    assert push_rejected_no_new_changes("", rejected)
    assert not push_rejected_no_new_changes("SUCCESS\n", "")


def test_gpush_yes_lazy_no_new_changes_still_applies_rest(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "gerrit.webUrl", "https://g.example.test", cwd=stack_repo)
    git("config", "gerrit.user", "testuser", cwd=stack_repo)
    git("config", "gerrit.password", "testpass", cwd=stack_repo)
    ready = compute_ready(stack_repo, stop_pattern=r"^test!")
    assert ready.push_range
    rows = commits_in_range(stack_repo, ready.push_range, first_parent=True)
    details = build_details_by_change_id(rows, per_index_overrides=[{"reviewers": []} for _ in rows])
    mock_run = MagicMock(
        return_value=subprocess.CompletedProcess(
            ["git", "push"],
            1,
            stdout=b"",
            stderr=b"! [remote rejected] tip -> refs/for/main (no new changes)\n",
        )
    )
    monkeypatch.setattr("gerrit_workflow_tools.core.push.run_git_push", mock_run)
    monkeypatch.setattr(sys, "stdin", _StdinNonTTY())
    code, out, _err = run_cli(
        stack_repo,
        gpush_main,
        ["--yes", "--reviewer-strategy", "lazy", "--reviewers", "alice"],
        monkeypatch,
        gerrit=ChangeStore(details),
    )
    assert code == 0
    assert any(" assigned " in ln and "r=alice" in ln for ln in out.splitlines())


def test_gpush_with_nothing_new_says_so_and_succeeds(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rejected = "! [remote rejected] tip -> refs/for/main (no new changes)\nerror: failed to push some refs\n"
    mock_run = MagicMock(return_value=subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=rejected))
    monkeypatch.setattr("gerrit_workflow_tools.core.push.run_git_push", mock_run)

    code, out, err = run_cli(stack_repo, gpush_main, ["-y"], monkeypatch)

    assert code == 0
    assert "nothing to push" in out
    assert "remote rejected" not in err


def test_gpush_warns_about_a_commit_on_an_abandoned_change(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = commits_in_range(stack_repo, "main..feature")
    details = build_details_by_change_id(rows, per_index_overrides=[{"status": "ABANDONED"}])
    code, _out, err = run_cli(stack_repo, gpush_main, ["--dry-run"], monkeypatch, gerrit=ChangeStore(details))
    assert code == 0
    assert f"warning: {rows[0].short_sha} targets abandoned change 100" in err


def test_gpush_names_a_fixup_commit_as_the_boundary(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("commit", "--allow-empty", "--fixup", "HEAD~1", cwd=stack_repo)
    code, out, _err = run_cli(stack_repo, gpush_main, ["--dry-run", "--all"], monkeypatch)
    assert code == 0
    assert "git rebase -i --autosquash" in out
    assert "no Change-Id" not in out


def test_gpush_says_which_commits_missed_the_push_options(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = commits_in_range(stack_repo, "main..feature")[:2]
    details = build_details_by_change_id(rows[:1])
    mock_run = MagicMock(return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""))
    monkeypatch.setattr("gerrit_workflow_tools.core.push.run_git_push", mock_run)

    code, _out, err = run_cli(stack_repo, gpush_main, ["-y", "--topic", "t"], monkeypatch, gerrit=ChangeStore(details))

    assert code == 0
    assert f"warning: topic not applied to unchanged commits: {rows[0].short_sha}" in err


def test_gpush_refuses_a_comma_in_the_topic(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, _out, err = run_cli(stack_repo, gpush_main, ["--dry-run", "--topic", "x,y"], monkeypatch)
    assert code == 1
    assert "invalid topic 'x,y'" in err


def test_gpush_forgets_changes_that_were_cached_as_missing(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A change that did not exist before the push is visible right after it, without ``ger cache clear``."""
    commits = commits_in_range(stack_repo, "main..feature")[:2]
    code, out, _err = run_cli(stack_repo, log_main, [], monkeypatch, gerrit=ChangeStore({}))
    assert "not-pushed" in out

    pushed = ChangeStore(build_details_by_change_id(commits))
    mock_run = MagicMock(return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""))
    monkeypatch.setattr("gerrit_workflow_tools.core.push.run_git_push", mock_run)
    code, _out, _err = run_cli(stack_repo, gpush_main, ["-y"], monkeypatch, gerrit=pushed)
    assert code == 0

    _code, out, _err = run_cli(stack_repo, log_main, [], monkeypatch, gerrit=pushed)
    assert out.count("not-pushed") == 2 and "on-gerrit 2" in out


def test_gpush_reviewer_failure_is_a_warning_and_still_records_the_push(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _RejectsAdds(ChangeStore):
        def set_reviewers_batch(self, change_id: str, **_kw: object) -> dict[str, object]:
            raise GerritApiError("Gerrit HTTP 400")

    git("config", "gerrit.webUrl", "https://g.example.test", cwd=stack_repo)
    git("config", "gerrit.user", "testuser", cwd=stack_repo)
    git("config", "gerrit.password", "testpass", cwd=stack_repo)
    rows = commits_in_range(stack_repo, "main..feature")[:2]
    details = build_details_by_change_id(rows, per_index_overrides=[{"reviewers": []} for _ in rows])
    mock_run = MagicMock(return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""))
    monkeypatch.setattr("gerrit_workflow_tools.core.push.run_git_push", mock_run)

    code, _out, err = run_cli(
        stack_repo,
        gpush_main,
        ["-y", "--reviewers", "nosuchuser", "--reviewer-strategy", "overwrite"],
        monkeypatch,
        gerrit=_RejectsAdds(details),
    )

    assert code == ExitCode.ATTENTION
    assert "warning: pushed, but could not update reviewers" in err
    assert "error:" not in err
    assert ref_exists(stack_repo, "refs/heads/lastPush/feature")


def test_gpush_lazy_reaches_freshly_pushed_changes(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The post-push reviewer pass must not read the pre-push "no such change" verdict."""
    git("config", "gerrit.webUrl", "https://g.example.test", cwd=stack_repo)
    git("config", "gerrit.user", "testuser", cwd=stack_repo)
    git("config", "gerrit.password", "testpass", cwd=stack_repo)
    commits = commits_in_range(stack_repo, "main..feature")[:2]
    run_cli(stack_repo, log_main, [], monkeypatch, gerrit=ChangeStore({}))
    details = build_details_by_change_id(commits, per_index_overrides=[{"reviewers": []}, {"reviewers": []}])
    mock_run = MagicMock(return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""))
    monkeypatch.setattr("gerrit_workflow_tools.core.push.run_git_push", mock_run)
    code, _out, err = run_cli(
        stack_repo,
        gpush_main,
        ["-y", "--reviewers", "alice", "--reviewer-strategy", "lazy"],
        monkeypatch,
        gerrit=ChangeStore(details),
    )
    assert code == 0, err
    assert "could not load change" not in err


def test_gpush_yes_overwrite_prints_per_commit_assignment_lines(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git("config", "gerrit.webUrl", "https://g.example.test", cwd=stack_repo)
    git("config", "gerrit.user", "testuser", cwd=stack_repo)
    git("config", "gerrit.password", "testpass", cwd=stack_repo)
    ready = compute_ready(stack_repo, stop_pattern=r"^test!")
    assert ready.push_range
    rows = commits_in_range(stack_repo, ready.push_range, first_parent=True)
    reviewer_existing = {
        "reviewers": [{"account": {"username": "bob", "_account_id": 3}, "state": "REVIEWER"}],
    }
    details = build_details_by_change_id(rows, per_index_overrides=[reviewer_existing] * len(rows))
    mock_run = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr("gerrit_workflow_tools.core.push.run_git_push", mock_run)
    monkeypatch.setattr(sys, "stdin", _StdinNonTTY())
    code, out, _err = run_cli(
        stack_repo,
        gpush_main,
        ["--yes", "--reviewer-strategy", "overwrite", "--reviewers", "alice,ben"],
        monkeypatch,
        gerrit=ChangeStore(details),
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
    monkeypatch.setattr("gerrit_workflow_tools.core.push.run_git_push", mock_run)
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
        ["--all"],
        ["--debug-log"],
    ],
    ids=["all", "debug-log"],
)
def test_gpush_dry_run_variants_exit_zero(stack_repo: Path, monkeypatch: pytest.MonkeyPatch, extra: list[str]) -> None:
    code, out, err = run_cli(stack_repo, gpush_main, ["--dry-run", *extra], monkeypatch)
    assert code == 0, (code, out, err)
    assert "About to push commits:" in out
    assert "git push" in out
    assert ":refs/for/" in out
    assert "[dry-run]" in err
    if "--all" in extra:
        assert "Cleanup after experiment" in out


def test_gpush_dry_run_highlights_warning_patterns(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = stack_rows_mb_to_head(stack_repo)
    first_subject = rows[0].subject
    git("config", "gerrit.stopPattern", r"^does-not-match$", cwd=stack_repo)
    git("config", "gerrit.warningPattern", f"^{re.escape(first_subject)}$", cwd=stack_repo)
    code, out, _err = run_cli(stack_repo, gpush_main, ["--dry-run", "--all", "--color", "always"], monkeypatch)
    assert code == 0
    assert ANSI_YELLOW in out
    assert first_subject in out


def test_gpush_dry_run_highlights_stop_boundary_subject(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = stack_rows_mb_to_head(stack_repo)
    assert len(rows) >= 2
    boundary_subject = rows[1].subject
    git("config", "gerrit.stopPattern", f"^{re.escape(boundary_subject)}$", cwd=stack_repo)
    code, out, _err = run_cli(stack_repo, gpush_main, ["--dry-run", "--color", "always"], monkeypatch)
    assert code == 0
    assert "Stopped at commit" in out
    assert ANSI_YELLOW in out
    assert boundary_subject in out


def test_gpush_show_attributes_fails_without_weburl(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "gerrit.pushShowAttributes", "true", cwd=stack_repo)
    code, _out, err = run_cli(stack_repo, gpush_main, ["--dry-run"], monkeypatch)
    assert code == 1
    assert "gerrit.webUrl" in err or "webUrl" in err


def test_gpush_show_attributes_fails_without_credentials(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "gerrit.webUrl", "https://g.example.test", cwd=stack_repo)
    git("config", "gerrit.pushShowAttributes", "true", cwd=stack_repo)
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
    code, out, _err = run_cli(
        stack_repo,
        gpush_main,
        ["--dry-run", "--reviewers", "alice"],
        monkeypatch,
        gerrit=ChangeStore(details),
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
    code, out, _err = run_cli(
        stack_repo,
        gpush_main,
        ["--dry-run", "--reviewers", "alice", "--reviewers", "bob"],
        monkeypatch,
        gerrit=ChangeStore(details),
    )
    assert code == 0
    assert "->" in out
    assert "`r=alice` -> `r=alice,r=bob`" in out


def test_gpush_push_show_attributes_false_skips_attribute_suffix(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git("config", "gerrit.webUrl", "https://g.example.test", cwd=stack_repo)
    git("config", "gerrit.user", "testuser", cwd=stack_repo)
    git("config", "gerrit.password", "testpass", cwd=stack_repo)
    git("config", "gerrit.pushShowAttributes", "false", cwd=stack_repo)
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
    code, out, _err = run_cli(
        stack_repo,
        gpush_main,
        ["--dry-run", "--reviewers", "alice"],
        monkeypatch,
        gerrit=ChangeStore(details),
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
    code, out, _err = run_cli(
        stack_repo,
        gpush_main,
        ["--dry-run", "--reviewers", "alice"],
        monkeypatch,
        gerrit=ChangeStore(details),
    )
    assert code == 0
    assert "`r=alice,wip`" in out
    assert "->" not in out


def test_gpush_interactive_reviewers_refspec(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", _StdinTTY())
    monkeypatch.setattr(
        "gerrit_workflow_tools.push_preview.prompt_reviewers_line",
        lambda *_a, **_k: parse_push_options_line("bob"),
    )
    monkeypatch.setattr("gerrit_workflow_tools.push_preview.prompt_save_reviewers", lambda: False)
    code, out, _err = run_cli(stack_repo, gpush_main, ["--dry-run", "-i"], monkeypatch)
    assert code == 0
    assert "%r=bob" in out


def test_gpush_interactive_reviewers_lazy_omits_percent_r(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", _StdinTTY())
    monkeypatch.setattr(
        "gerrit_workflow_tools.push_preview.prompt_reviewers_line",
        lambda *_a, **_k: parse_push_options_line("bob lazy"),
    )
    monkeypatch.setattr("gerrit_workflow_tools.push_preview.prompt_save_reviewers", lambda: False)
    code, out, err = run_cli(stack_repo, gpush_main, ["--dry-run", "-i"], monkeypatch)
    assert code == 0
    assert "lazy" in err
    line = next(ln for ln in out.splitlines() if "git push" in ln)
    assert "%r=" not in line


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
        "gerrit_workflow_tools.push_preview.prompt_reviewers_line",
        lambda *_a, **_k: parse_push_options_line("alice"),
    )
    mock_run = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr("gerrit_workflow_tools.core.push.run_git_push", mock_run)
    code, _out, _err = run_cli(stack_repo, gpush_main, [], monkeypatch)
    assert code == 0
    refspec = _mock_gerrit_push_refspec(mock_run)
    assert "%r=alice" in refspec


@pytest.mark.parametrize(("confirm_answer", "saved"), [("n", []), ("", ["r=alice"])])
def test_gpush_saves_typed_options_only_after_a_push(
    confirm_answer: str, saved: list[str], stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git("config", "gerrit.webUrl", "https://g.example.test", cwd=stack_repo)
    git("config", "gerrit.project", "proj", cwd=stack_repo)
    monkeypatch.setattr(sys, "stdin", _StdinTTY())
    answers = iter(["r", confirm_answer])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    monkeypatch.setattr(
        "gerrit_workflow_tools.push_preview.prompt_reviewers_line",
        lambda *_a, **_k: parse_push_options_line("alice"),
    )
    mock_run = MagicMock(return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""))
    monkeypatch.setattr("gerrit_workflow_tools.core.push.run_git_push", mock_run)

    code, _out, _err = run_cli(stack_repo, gpush_main, [], monkeypatch)

    assert code == 0
    assert load_push_options_history(web_base="https://g.example.test", project="proj") == saved


def _confirm_line_session(
    monkeypatch: pytest.MonkeyPatch, line: str | type[BaseException]
) -> tuple[MagicMock, list[dict[str, object]]]:
    """Make the confirmation the options input line, answering *line* (or raising it); mock ``git push``."""
    monkeypatch.setattr(sys, "stdin", _StdinTTY())
    monkeypatch.setattr("gerrit_workflow_tools.cli_push.options_line_available", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *_a: pytest.fail("the options line replaces the [Y/n/r] question"))
    calls: list[dict[str, object]] = []

    def _prompt(*_a: object, **kw: object) -> object:
        calls.append(kw)
        if isinstance(line, str):
            return parse_push_options_line(line)
        raise line

    monkeypatch.setattr("gerrit_workflow_tools.push_preview.prompt_reviewers_line", _prompt)
    mock_run = MagicMock(return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""))
    monkeypatch.setattr("gerrit_workflow_tools.core.push.run_git_push", mock_run)
    return mock_run, calls


def test_gpush_options_line_empty_pushes_without_reviewers(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mock_run, calls = _confirm_line_session(monkeypatch, "")
    code, _out, _err = run_cli(stack_repo, gpush_main, [], monkeypatch)
    assert code == 0
    assert "%r=" not in _mock_gerrit_push_refspec(mock_run)
    assert calls[0]["prefill"] is False


def test_gpush_options_line_typed_reviewers_ride_the_push(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mock_run, _calls = _confirm_line_session(monkeypatch, "alice,bob")
    code, _out, _err = run_cli(stack_repo, gpush_main, [], monkeypatch)
    assert code == 0
    refspec = _mock_gerrit_push_refspec(mock_run)
    assert refspec.endswith("%r=alice,bob")


def test_gpush_options_line_empty_keeps_reviewers_from_the_flag(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_run, _calls = _confirm_line_session(monkeypatch, "")
    code, _out, _err = run_cli(stack_repo, gpush_main, ["--reviewers", "carol"], monkeypatch)
    assert code == 0
    assert "r=carol" in _mock_gerrit_push_refspec(mock_run)


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, EOFError])
def test_gpush_options_line_interrupt_cancels(
    interrupt: type[BaseException], stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_run, _calls = _confirm_line_session(monkeypatch, interrupt)
    code, _out, err = run_cli(stack_repo, gpush_main, [], monkeypatch)
    assert code == 0
    assert "Push cancelled." in err
    mock_run.assert_not_called()


def test_gpush_interactive_reviewers_overwrites_branch_and_cli(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    branch = git_out("rev-parse", "--abbrev-ref", "HEAD", cwd=stack_repo)
    set_branch_config(stack_repo, branch, gerrit_reviewers="carol")
    monkeypatch.setattr(sys, "stdin", _StdinTTY())
    monkeypatch.setattr(
        "gerrit_workflow_tools.push_preview.prompt_reviewers_line",
        lambda *_a, **_k: parse_push_options_line("bob"),
    )
    monkeypatch.setattr("gerrit_workflow_tools.push_preview.prompt_save_reviewers", lambda: False)
    code, out, _err = run_cli(
        stack_repo,
        gpush_main,
        ["--dry-run", "-i", "--reviewers", "alice"],
        monkeypatch,
    )
    assert code == 0
    refspec = next(ln for ln in out.splitlines() if "git push" in ln)
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
    code, _out, err = run_cli(stack_repo, gpush_main, ["--dry-run", "-i", "--yes"], monkeypatch, catch_sys_exit=True)
    assert code == ExitCode.USAGE
    assert "-i" in err


def test_gpush_success_updates_last_push_branch(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "gerrit.lastPushedBranch", "true", cwd=stack_repo)
    expected_tip = compute_ready(stack_repo, stop_pattern=r"^test!").push_tip_sha
    assert expected_tip
    monkeypatch.setattr(sys, "stdin", _StdinNonTTY())
    mock_run = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr("gerrit_workflow_tools.core.push.run_git_push", mock_run)
    code, _out, _err = run_cli(stack_repo, gpush_main, ["--yes"], monkeypatch)
    assert code == 0
    assert git_out("rev-parse", "lastPush/feature", cwd=stack_repo) == expected_tip


def test_gpush_skips_last_push_when_config_false(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "gerrit.lastPushedBranch", "false", cwd=stack_repo)
    monkeypatch.setattr(sys, "stdin", _StdinNonTTY())
    mock_run = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr("gerrit_workflow_tools.core.push.run_git_push", mock_run)
    code, _out, _err = run_cli(stack_repo, gpush_main, ["--yes"], monkeypatch)
    assert code == 0
    assert not ref_exists(stack_repo, "refs/heads/lastPush/feature")


def test_gpush_dry_run_does_not_create_last_push_branch(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "gerrit.lastPushedBranch", "true", cwd=stack_repo)
    code, _out, _err = run_cli(stack_repo, gpush_main, ["--dry-run"], monkeypatch)
    assert code == 0
    assert not ref_exists(stack_repo, "refs/heads/lastPush/feature")


def test_gpush_failed_push_does_not_update_last_push(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "gerrit.lastPushedBranch", "true", cwd=stack_repo)
    monkeypatch.setattr(sys, "stdin", _StdinNonTTY())
    mock_run = MagicMock(return_value=MagicMock(returncode=1))
    monkeypatch.setattr("gerrit_workflow_tools.core.push.run_git_push", mock_run)
    code, _out, _err = run_cli(stack_repo, gpush_main, ["--yes"], monkeypatch)
    assert code == 1
    assert not ref_exists(stack_repo, "refs/heads/lastPush/feature")


def test_gpush_dry_run_with_merged_side_branch_lists_only_first_parent_commits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Regression: the push preview must not list side-branch commits.

    Only the two first-parent commits (local work + merge commit) should
    appear in the 'About to push commits:' output.
    """
    repo = make_repo_with_merged_side_branch(tmp_path / "r")
    code, out, err = run_cli(repo, gpush_main, ["--dry-run", "--all"], monkeypatch)
    assert code == 0, (out, err)
    assert "local work" in out
    assert "Merge side branch" in out
    assert "side commit 1" not in out
    assert "side commit 2" not in out


def test_gpush_follow_merges_flag_lists_side_branch_commits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--follow-merges`` must re-include side-branch commits in the push preview."""
    repo = make_repo_with_merged_side_branch(tmp_path / "r")
    code, out, err = run_cli(repo, gpush_main, ["--dry-run", "--all", "--follow-merges"], monkeypatch)
    assert code == 0, (out, err)
    assert "side commit 1" in out
    assert "side commit 2" in out


def _add_self_origin_and_fetch(repo: Path) -> None:
    if git("remote", "get-url", "origin", cwd=repo, check=False).returncode != 0:
        git("remote", "add", "origin", str(repo.resolve()), cwd=repo)
    git("fetch", "origin", cwd=repo)
    git("config", "gerrit.project", "testproj", cwd=repo, check=False)


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
    code, out, err = run_cli(stack_repo, gpush_main, ["--dry-run"], monkeypatch)
    assert code == 0
    assert "About to push commits:" in out
    assert "not based directly" not in err.lower()


def test_gpush_warn_not_rebased_when_remote_ahead(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _add_self_origin_and_fetch(stack_repo)
    _advance_main_with_commit(stack_repo)
    git("config", "gerrit.push.remotePolicy", "warn-not-rebased", cwd=stack_repo)
    code, out, err = run_cli(stack_repo, gpush_main, ["--dry-run"], monkeypatch)
    assert code == 0
    assert "About to push commits:" in out
    assert "warning:" in err.lower()
    assert "ger rebase --onto-remote" in err


def test_gpush_error_not_rebased_exits(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _add_self_origin_and_fetch(stack_repo)
    _advance_main_with_commit(stack_repo)
    git("config", "gerrit.push.remotePolicy", "error-not-rebased", cwd=stack_repo)
    code, _out, err = run_cli(stack_repo, gpush_main, ["--dry-run"], monkeypatch)
    assert code == 1
    assert "error:" in err.lower()
    assert "ger rebase --onto-remote" in err


def test_gpush_no_rebase_check_bypasses_error_policy(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _add_self_origin_and_fetch(stack_repo)
    _advance_main_with_commit(stack_repo)
    git("config", "gerrit.push.remotePolicy", "error-not-rebased", cwd=stack_repo)
    code, out, err = run_cli(stack_repo, gpush_main, ["--dry-run", "--no-rebase-check"], monkeypatch)
    assert code == 0
    assert "About to push commits:" in out
    assert "not based directly" not in err.lower()


def test_gpush_warn_policy_skips_when_fetch_impossible(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from gerrit_workflow_tools.core.git_run import GitError

    def git_fetch_fail(*args, **kwargs):
        if args and args[0] == "fetch":
            raise GitError("git fetch origin failed: simulated fetch failure")
        return git(*args, **kwargs)

    monkeypatch.setattr("gerrit_workflow_tools.core.push_checks.git", git_fetch_fail)
    git("config", "gerrit.push.remotePolicy", "warn-not-rebased", cwd=stack_repo)
    code, out, err = run_cli(stack_repo, gpush_main, ["--dry-run"], monkeypatch)
    assert code == 0, err
    assert "About to push commits:" in out
    assert "skipping remote rebase check" in err.lower()
