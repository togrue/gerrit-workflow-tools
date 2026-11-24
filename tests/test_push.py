# Spec: docu/spec/commands/push.md
# Covers: --dry-run, --yes, duplicate Change-Id, lastPush branch, merged side branch, reviewer strategies

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_log import main as log_main
from gerrit_workflow_tools.cli_push import main as gpush_main
from gerrit_workflow_tools.core.config_write import set_branch_config
from gerrit_workflow_tools.core.gerrit.rest import GerritApiError
from gerrit_workflow_tools.core.git_run import git, git_out
from gerrit_workflow_tools.core.ready_calc import compute_ready
from gerrit_workflow_tools.core.stack import Commit, commits_in_range
from gerrit_workflow_tools.push_input_line import parse as parse_push_options_line
from gerrit_workflow_tools.push_options_history import load_push_options_history
from gerrit_workflow_tools.render.style import ANSI_YELLOW
from tests.change_store import ChangeStore
from tests.cli_gerrit_mocks import (
    at_older_patchsets,
    build_details_by_change_id,
    stack_rows_mb_to_head,
    web_edit,
)
from tests.conftest import run_cli
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
    assert "--private" in out and "--no-private" in out
    scrubbed = out.replace("--reviewers", "").replace("--reviewer-strategy", "")
    assert "--reviewer" not in scrubbed
    assert "--no-rebase-check" in out
    assert "-i" in out
    assert "--follow-merges" in out


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
    assert code == ExitCode.CONFIG
    assert "push destination" in err.lower() or "upstream" in err.lower()


def test_gpush_prompts_for_missing_upstream_and_aborts(
    stack_repo_unconfigured: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = stack_repo_unconfigured
    git("branch", "--unset-upstream", "feature", cwd=repo, check=False)
    monkeypatch.setattr(sys, "stdin", _StdinTTY())
    monkeypatch.setattr(
        "gerrit_workflow_tools.upstream_interactive.prompt_upstream_abbrev_interactive",
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
        "gerrit_workflow_tools.upstream_interactive.prompt_upstream_abbrev_interactive",
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
        "gerrit_workflow_tools.upstream_interactive.prompt_upstream_abbrev_interactive",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("--dry-run must not prompt for an upstream")),
    )
    code, _out, err = run_cli(repo, gpush_main, ["--dry-run"], monkeypatch)
    assert code == ExitCode.CONFIG
    assert "No upstream configured for branch" in err
    assert git("rev-parse", "--abbrev-ref", "feature@{upstream}", cwd=repo, check=False).returncode != 0


def test_gpush_vanilla_upstream_runs_plain_git_push(
    stack_repo_unconfigured: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Upstream on a remote other than ger.remote → ``git push`` with no extra args (B1)."""
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


def test_gpush_refuses_while_a_rebase_is_underway(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The branch ref moves only when the rebase ends: pushing now would send the stack from before it."""
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

    assert code == ExitCode.USAGE
    assert ":refs/for/main" not in out
    assert "finish it (`git rebase --continue`) or `git rebase --abort`, then push" in err


def test_gpush_detached_head_does_not_prompt_upstream(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("checkout", "--detach", "HEAD", cwd=stack_repo)
    monkeypatch.setattr(sys, "stdin", _StdinTTY())
    monkeypatch.setattr(
        "gerrit_workflow_tools.upstream_interactive.prompt_upstream_abbrev_interactive",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("upstream prompt on detached HEAD")),
    )
    code, _out, _err = run_cli(stack_repo, gpush_main, ["--dry-run"], monkeypatch)
    assert code == 0


def test_gpush_branch_flag_unknown_branch_errors(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, _out, err = run_cli(stack_repo, gpush_main, ["--branch", "no-such-branch", "--dry-run"], monkeypatch)
    assert code == ExitCode.NOT_FOUND
    assert "no-such-branch" in err


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
    monkeypatch.setattr("gerrit_workflow_tools.cli_terminal._is_windows_console", lambda _stream: False)
    monkeypatch.setattr("builtins.input", lambda _prompt="": pytest.fail("must not prompt"))

    code, _out, err = run_cli(stack_repo, gpush_main, [], monkeypatch)

    assert code == ExitCode.USAGE
    assert "--yes" in err


def test_gpush_i_treats_windows_nul_as_non_interactive(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", _StdinTTY())
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr("gerrit_workflow_tools.cli_terminal._is_windows_console", lambda _stream: False)

    code, _out, err = run_cli(stack_repo, gpush_main, ["--dry-run", "-i"], monkeypatch)

    assert code == ExitCode.USAGE
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
    assert code == ExitCode.USAGE
    assert "non-interactive" in err.lower() or "--yes" in err
    mock_run.assert_not_called()


def test_gpush_cancel_at_prompt(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "ger.lastPushedBranch", "true", cwd=stack_repo)
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
    i_remain = out.index("2 commits from ")
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


def test_gpush_yes_lazy_no_new_changes_still_applies_rest(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "ger.webUrl", "https://g.example.test", cwd=stack_repo)
    git("config", "ger.user", "testuser", cwd=stack_repo)
    git("config", "ger.password", "testpass", cwd=stack_repo)
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


def test_gpush_rejected_by_gerrit_keeps_gits_output_and_adds_gerrits_reason(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """git's own lines pass through untouched; one ger line after them says why; git's exit code is kept."""
    rejected = (
        " ! [remote rejected] tip -> refs/for/main (Account 'nosuch' not found\n"
        "nosuch does not identify a registered user or group)\n"
        "error: failed to push some refs to 'ssh://g.example.test/proj'\n"
    )
    mock_run = MagicMock(return_value=subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=rejected))
    monkeypatch.setattr("gerrit_workflow_tools.core.push.run_git_push", mock_run)

    code, _out, err = run_cli(stack_repo, gpush_main, ["-y", "--reviewers", "nosuch"], monkeypatch)

    assert code == 1
    assert err.endswith(
        rejected + "error: Gerrit rejected the push: Account 'nosuch' not found; "
        "nosuch does not identify a registered user or group\n"
    )
    assert not ref_exists(stack_repo, "refs/heads/lastPush/feature")


def test_gpush_does_not_list_a_commit_of_an_abandoned_change_as_sent(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before: a warning, the commit under "About to push commits", then "nothing to push"."""
    rows = commits_in_range(stack_repo, "main..feature")[:2]
    details = build_details_by_change_id(rows[:1], per_index_overrides=[{"status": "ABANDONED"}])
    details |= at_older_patchsets(build_details_by_change_id(rows[1:2]), stack_repo)
    mock_run = MagicMock(return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""))
    monkeypatch.setattr("gerrit_workflow_tools.core.push.run_git_push", mock_run)
    code, out, err = run_cli(stack_repo, gpush_main, ["-y", rows[1].sha], monkeypatch, gerrit=ChangeStore(details))
    assert code == 0
    sent = [line for line in out.split("About to push commits:")[1].splitlines() if line.startswith("    ")]
    assert [line.split()[0] for line in sent] == [rows[1].short_sha]
    assert (
        f"{rows[0].short_sha} not sent: change 100 is abandoned "
        f"(restore it in the web UI, or `ger edit --drop {rows[0].short_sha}`)" in out
    )
    assert "warning" not in err and "commits Gerrit already has" not in out
    mock_run.assert_called_once()


def test_gpush_warns_before_a_new_patchset_for_an_abandoned_change(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A commit Gerrit does not have yet goes up, and Gerrit refuses it: the warning says so first."""
    rows = commits_in_range(stack_repo, "main..feature")
    details = build_details_by_change_id(rows, per_index_overrides=[{"status": "ABANDONED"}])
    code, _out, err = run_cli(
        stack_repo, gpush_main, ["--dry-run"], monkeypatch, gerrit=ChangeStore(at_older_patchsets(details, stack_repo))
    )
    assert code == 0
    assert f"warning: {rows[0].short_sha} would be a new patch set of abandoned change 100, which Gerrit refuses" in err


def test_gpush_names_a_fixup_commit_as_the_boundary(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("commit", "--allow-empty", "--fixup", "HEAD~1", cwd=stack_repo)
    code, out, _err = run_cli(stack_repo, gpush_main, ["--dry-run", "--all"], monkeypatch)
    assert code == 0
    assert "ger rebase --autosquash" in out
    assert "no Change-Id" not in out


def _rejected_as_unchanged(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    rejected = "! [remote rejected] tip -> refs/for/main (no new changes)\n"
    mock_run = MagicMock(return_value=subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=rejected))
    monkeypatch.setattr("gerrit_workflow_tools.core.push.run_git_push", mock_run)
    return mock_run


def test_gpush_ready_reaches_changes_gerrit_already_has_and_log_agrees(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ger log`` advertises ``ger push --ready`` for WIP changes; it must work when nothing is new."""
    rows = commits_in_range(stack_repo, "main..feature")[:2]
    store = ChangeStore(build_details_by_change_id(rows, per_index_overrides=[{"work_in_progress": True}] * 2))
    _code, out, _err = run_cli(stack_repo, log_main, [], monkeypatch, gerrit=store)
    assert "ready them with `ger push --ready`" in out
    _rejected_as_unchanged(monkeypatch)

    code, out, err = run_cli(stack_repo, gpush_main, ["-y", "--ready", rows[1].sha], monkeypatch, gerrit=store)

    assert code == 0, err
    assert "nothing to push" not in out and "About to push" not in out
    assert [call.args[1] for call in store.calls_to("set_wip")] == [False, False]
    assert all(f"set {row.short_sha}" in out and "ready" in out for row in rows)
    _code, out, _err = run_cli(stack_repo, log_main, [], monkeypatch, gerrit=store)
    assert "WIP" not in out.split("summary:")[-1]


def test_gpush_preview_names_what_it_sets_over_rest(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = commits_in_range(stack_repo, "main..feature")[:2]
    store = ChangeStore(build_details_by_change_id(rows[:1]))

    code, out, _err = run_cli(stack_repo, gpush_main, ["--dry-run", "--wip", "--topic", "t"], monkeypatch, gerrit=store)

    assert code == 0
    assert "set over REST" in out
    assert f"{rows[0].short_sha}  # {rows[0].subject}  wip,topic=t" in out
    assert rows[1].short_sha not in out.split("set over REST")[1].split("Branch")[0]
    assert "git push origin" in out  # the second commit is new
    assert not store.calls_to("set_wip") and not store.calls_to("set_topic")


def test_gpush_sets_only_what_differs_and_nothing_without_options(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = commits_in_range(stack_repo, "main..feature")[:2]
    store = ChangeStore(build_details_by_change_id(rows, per_index_overrides=[{}, {"work_in_progress": True}]))
    _rejected_as_unchanged(monkeypatch)

    code, _out, _err = run_cli(stack_repo, gpush_main, ["-y", rows[1].sha], monkeypatch, gerrit=store)
    assert code == 0
    writes = ("set_wip", "set_topic", "set_private", "set_reviewers_batch")
    assert not any(store.calls_to(name) for name in writes)

    code, out, _err = run_cli(stack_repo, gpush_main, ["-y", "--wip", rows[1].sha], monkeypatch, gerrit=store)
    assert code == 0
    assert len(store.calls_to("set_wip")) == 1 and f"set {rows[0].short_sha}" in out


@pytest.mark.parametrize("argv", [[], ["-y"]])
def test_gpush_with_nothing_to_send_or_set_says_so_without_asking(
    argv: list[str], stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No preview, no question, no ``-y`` needed off a terminal, and no ``git push`` Gerrit would refuse."""
    rows = commits_in_range(stack_repo, "main..feature")[:2]
    mock_run = _rejected_as_unchanged(monkeypatch)
    monkeypatch.setattr(sys, "stdin", _StdinNonTTY())
    monkeypatch.setattr("builtins.input", lambda _prompt="": pytest.fail("must not prompt"))

    code, out, err = run_cli(
        stack_repo, gpush_main, [*argv, rows[1].sha], monkeypatch, gerrit=ChangeStore(build_details_by_change_id(rows))
    )

    assert (code, err) == (ExitCode.OK, "")
    assert out.strip().endswith(
        "held back by the ready boundary; `ger push --all` sends them as work-in-progress\n"
        "nothing to push: Gerrit already has these commits"
    )
    mock_run.assert_not_called()


def test_gpush_dry_run_shows_no_git_push_the_real_run_would_not_make(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = commits_in_range(stack_repo, "main..feature")[:2]
    store = ChangeStore(build_details_by_change_id(rows))

    code, out, err = run_cli(stack_repo, gpush_main, ["--dry-run", "--wip", rows[1].sha], monkeypatch, gerrit=store)

    assert code == 0
    assert f"{rows[0].short_sha}  # {rows[0].subject}  wip" in out.split("No new commits to push; set on Gerrit:")[1]
    assert "git push" not in out and "About to push" not in out
    assert "[dry-run] no git push: Gerrit already has these commits" in err


def test_gpush_a_failed_rest_update_is_an_error_and_exit_gerrit(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _RejectsTopic(ChangeStore):
        def set_topic(self, change_id: str, topic: str | None) -> None:
            raise GerritApiError("Gerrit HTTP 403")

    rows = commits_in_range(stack_repo, "main..feature")[:1]
    _rejected_as_unchanged(monkeypatch)
    code, out, err = run_cli(
        stack_repo,
        gpush_main,
        ["-y", "--topic", "t", rows[0].sha],
        monkeypatch,
        gerrit=_RejectsTopic(build_details_by_change_id(rows)),
    )
    assert code == ExitCode.GERRIT
    assert f"error: could not set {rows[0].short_sha}" in err and "Gerrit HTTP 403" in err
    assert "nothing to push" not in out and "warning:" not in err


def test_gpush_an_unknown_reviewer_is_said_once_with_gerrits_reason(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same refusal on every unchanged commit is one line naming them, and the Gerrit exit code."""
    reason = "Gerrit HTTP 400: nosuchuser does not identify a registered user or group"

    class _UnknownReviewer(ChangeStore):
        def set_reviewers_batch(self, change_id: str, **_kw: object) -> dict[str, object]:
            raise GerritApiError(reason, status=400)

    rows = commits_in_range(stack_repo, "main..feature")[:2]
    _rejected_as_unchanged(monkeypatch)
    store = _UnknownReviewer(
        build_details_by_change_id(rows, per_index_overrides=[{"reviewers": []}] * 2),
        accounts={7: {"_account_id": 7, "username": "nosuchuser"}},  # an account it has, yet refuses to add
    )
    code, _out, err = run_cli(
        stack_repo, gpush_main, ["-y", "--reviewers", "nosuchuser", rows[1].sha], monkeypatch, gerrit=store
    )

    assert code == ExitCode.GERRIT
    errors = [line for line in err.splitlines() if line.startswith(("warning:", "error:"))]
    assert errors == [
        f"error: could not set r=nosuchuser on 2 commits ({rows[0].short_sha}, {rows[1].short_sha}): {reason}"
    ]


def test_gpush_that_only_sets_options_says_so_and_asks_to_set_them(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every commit on Gerrit: no "About to push" list, no "nothing to push", and the question names the REST sets."""
    rows = commits_in_range(stack_repo, "main..feature")[:2]
    store = ChangeStore(build_details_by_change_id(rows))
    mock_run = _rejected_as_unchanged(monkeypatch)
    monkeypatch.setattr(sys, "stdin", _StdinTTY())
    prompts: list[str] = []

    def _input(prompt: str = "") -> str:
        prompts.append(prompt)
        return "y"

    monkeypatch.setattr("builtins.input", _input)

    code, out, err = run_cli(stack_repo, gpush_main, ["--wip", rows[1].sha], monkeypatch, gerrit=store)

    assert code == 0, err
    assert prompts == ["Set these on Gerrit? [Y/n/r]: "]
    preview, _, after = out.partition("Branch")
    assert "About to push" not in out and "nothing to push" not in out
    assert preview.count("No new commits to push; set on Gerrit:") == 1
    assert [line.split()[0] for line in after.splitlines() if line.startswith("set ")] == ["set"] * 2
    assert len(store.calls_to("set_wip")) == 2
    mock_run.assert_not_called()


def test_gpush_lists_only_the_commits_it_sends(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A commit Gerrit already has, with nothing to set, is one line under the list, not in it."""
    rows = commits_in_range(stack_repo, "main..feature")[:2]
    store = ChangeStore(build_details_by_change_id(rows[:1]))

    code, out, _err = run_cli(stack_repo, gpush_main, ["--dry-run", rows[1].sha], monkeypatch, gerrit=store)

    assert code == 0
    listed = out.split("About to push commits:")[1].split("Branch")[0]
    assert rows[1].short_sha in listed and rows[0].short_sha not in listed
    assert "1 commit Gerrit already has is not sent again" in listed
    assert "set over REST" not in out
    assert "git push origin" in out


def test_gpush_refuses_a_comma_in_the_topic(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, _out, err = run_cli(stack_repo, gpush_main, ["--dry-run", "--topic", "x,y"], monkeypatch)
    assert code == ExitCode.USAGE
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

    git("config", "ger.webUrl", "https://g.example.test", cwd=stack_repo)
    git("config", "ger.user", "testuser", cwd=stack_repo)
    git("config", "ger.password", "testpass", cwd=stack_repo)
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

    assert code == ExitCode.GERRIT
    assert "warning: pushed, but could not update reviewers" in err
    assert "error:" not in err
    assert ref_exists(stack_repo, "refs/heads/lastPush/feature")


def test_gpush_lazy_reaches_freshly_pushed_changes(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The post-push reviewer pass must not read the pre-push "no such change" verdict."""
    git("config", "ger.webUrl", "https://g.example.test", cwd=stack_repo)
    git("config", "ger.user", "testuser", cwd=stack_repo)
    git("config", "ger.password", "testpass", cwd=stack_repo)
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
    git("config", "ger.webUrl", "https://g.example.test", cwd=stack_repo)
    git("config", "ger.user", "testuser", cwd=stack_repo)
    git("config", "ger.password", "testpass", cwd=stack_repo)
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
    git("config", "ger.stopPattern", r"^does-not-match$", cwd=stack_repo)
    git("config", "ger.warningPattern", f"^{re.escape(first_subject)}$", cwd=stack_repo)
    code, out, _err = run_cli(stack_repo, gpush_main, ["--dry-run", "--all", "--color", "always"], monkeypatch)
    assert code == 0
    assert ANSI_YELLOW in out
    assert first_subject in out


def test_gpush_dry_run_highlights_stop_boundary_subject(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = stack_rows_mb_to_head(stack_repo)
    assert len(rows) >= 2
    boundary_subject = rows[1].subject
    git("config", "ger.stopPattern", f"^{re.escape(boundary_subject)}$", cwd=stack_repo)
    code, out, _err = run_cli(stack_repo, gpush_main, ["--dry-run", "--color", "always"], monkeypatch)
    assert code == 0
    assert "Stopped at commit" in out
    assert ANSI_YELLOW in out
    assert boundary_subject in out


def test_gpush_show_attributes_fails_without_weburl(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "ger.pushShowAttributes", "true", cwd=stack_repo)
    code, _out, err = run_cli(stack_repo, gpush_main, ["--dry-run"], monkeypatch)
    assert code == 1
    assert "ger.webUrl" in err or "webUrl" in err


def test_gpush_show_attributes_fails_without_credentials(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "ger.webUrl", "https://g.example.test", cwd=stack_repo)
    git("config", "ger.pushShowAttributes", "true", cwd=stack_repo)
    code, _out, err = run_cli(stack_repo, gpush_main, ["--dry-run"], monkeypatch)
    assert code == 1
    assert "credentials" in err.lower()


def test_gpush_show_attributes_unchanged_when_matching_reviewers(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git("config", "ger.webUrl", "https://g.example.test", cwd=stack_repo)
    git("config", "ger.user", "testuser", cwd=stack_repo)
    git("config", "ger.password", "testpass", cwd=stack_repo)
    git("config", "ger.pushShowAttributes", "true", cwd=stack_repo)
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
        gerrit=ChangeStore(at_older_patchsets(details, stack_repo)),
    )
    assert code == 0
    assert "About to push commits:" in out
    assert "`r=alice`" in out
    assert "->" not in out


def test_gpush_show_attributes_shows_arrow_when_reviewers_differ(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git("config", "ger.webUrl", "https://g.example.test", cwd=stack_repo)
    git("config", "ger.user", "testuser", cwd=stack_repo)
    git("config", "ger.password", "testpass", cwd=stack_repo)
    git("config", "ger.pushShowAttributes", "true", cwd=stack_repo)
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
        gerrit=ChangeStore(
            at_older_patchsets(details, stack_repo), accounts={7: {"_account_id": 7, "username": "bob"}}
        ),
    )
    assert code == 0
    assert "->" in out
    assert "`r=alice` -> `r=alice,r=bob`" in out


def test_gpush_show_attributes_wip_no_arrow_when_reviewers_match(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git("config", "ger.webUrl", "https://g.example.test", cwd=stack_repo)
    git("config", "ger.user", "testuser", cwd=stack_repo)
    git("config", "ger.password", "testpass", cwd=stack_repo)
    git("config", "ger.pushShowAttributes", "true", cwd=stack_repo)
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
        gerrit=ChangeStore(at_older_patchsets(details, stack_repo)),
    )
    assert code == 0
    assert "`r=alice,wip`" in out
    assert "->" not in out


def test_gpush_interactive_reviewers_refspec(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", _StdinTTY())
    monkeypatch.setattr(
        "gerrit_workflow_tools.push_confirm.prompt_reviewers_line",
        lambda *_a, **_k: parse_push_options_line("bob"),
    )
    monkeypatch.setattr("gerrit_workflow_tools.push_confirm.prompt_save_reviewers", lambda: False)
    code, out, _err = run_cli(stack_repo, gpush_main, ["--dry-run", "-i"], monkeypatch)
    assert code == 0
    assert "%r=bob" in out


def test_gpush_interactive_reviewers_lazy_omits_percent_r(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", _StdinTTY())
    monkeypatch.setattr(
        "gerrit_workflow_tools.push_confirm.prompt_reviewers_line",
        lambda *_a, **_k: parse_push_options_line("bob lazy"),
    )
    monkeypatch.setattr("gerrit_workflow_tools.push_confirm.prompt_save_reviewers", lambda: False)
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
        "gerrit_workflow_tools.push_confirm.prompt_reviewers_line",
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
    git("config", "ger.webUrl", "https://g.example.test", cwd=stack_repo)
    git("config", "ger.project", "proj", cwd=stack_repo)
    monkeypatch.setattr(sys, "stdin", _StdinTTY())
    answers = iter(["r", confirm_answer])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    monkeypatch.setattr(
        "gerrit_workflow_tools.push_confirm.prompt_reviewers_line",
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
    monkeypatch.setattr("gerrit_workflow_tools.push_confirm.options_line_available", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *_a: pytest.fail("the options line replaces the [Y/n/r] question"))
    calls: list[dict[str, object]] = []

    def _prompt(*_a: object, **kw: object) -> object:
        calls.append(kw)
        if isinstance(line, str):
            return parse_push_options_line(line)
        raise line

    monkeypatch.setattr("gerrit_workflow_tools.push_confirm.prompt_reviewers_line", _prompt)
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
        "gerrit_workflow_tools.push_confirm.prompt_reviewers_line",
        lambda *_a, **_k: parse_push_options_line("bob"),
    )
    monkeypatch.setattr("gerrit_workflow_tools.push_confirm.prompt_save_reviewers", lambda: False)
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
    assert code == ExitCode.USAGE
    assert "tty" in err.lower()


def test_gpush_interactive_forbidden_with_yes(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", _StdinTTY())
    code, _out, err = run_cli(stack_repo, gpush_main, ["--dry-run", "-i", "--yes"], monkeypatch, catch_sys_exit=True)
    assert code == ExitCode.USAGE
    assert "-i" in err


def test_gpush_success_updates_last_push_branch(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "ger.lastPushedBranch", "true", cwd=stack_repo)
    expected_tip = compute_ready(stack_repo, stop_pattern=r"^test!").push_tip_sha
    assert expected_tip
    monkeypatch.setattr(sys, "stdin", _StdinNonTTY())
    mock_run = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr("gerrit_workflow_tools.core.push.run_git_push", mock_run)
    code, _out, _err = run_cli(stack_repo, gpush_main, ["--yes"], monkeypatch)
    assert code == 0
    assert git_out("rev-parse", "lastPush/feature", cwd=stack_repo) == expected_tip


def test_gpush_skips_last_push_when_config_false(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "ger.lastPushedBranch", "false", cwd=stack_repo)
    monkeypatch.setattr(sys, "stdin", _StdinNonTTY())
    mock_run = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr("gerrit_workflow_tools.core.push.run_git_push", mock_run)
    code, _out, _err = run_cli(stack_repo, gpush_main, ["--yes"], monkeypatch)
    assert code == 0
    assert not ref_exists(stack_repo, "refs/heads/lastPush/feature")


def test_gpush_dry_run_does_not_create_last_push_branch(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "ger.lastPushedBranch", "true", cwd=stack_repo)
    code, _out, _err = run_cli(stack_repo, gpush_main, ["--dry-run"], monkeypatch)
    assert code == 0
    assert not ref_exists(stack_repo, "refs/heads/lastPush/feature")


def test_gpush_failed_push_does_not_update_last_push(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "ger.lastPushedBranch", "true", cwd=stack_repo)
    monkeypatch.setattr(sys, "stdin", _StdinNonTTY())
    mock_run = MagicMock(return_value=MagicMock(returncode=1))
    monkeypatch.setattr("gerrit_workflow_tools.core.push.run_git_push", mock_run)
    code, _out, _err = run_cli(stack_repo, gpush_main, ["--yes"], monkeypatch)
    assert code == 1
    assert not ref_exists(stack_repo, "refs/heads/lastPush/feature")


def test_gpush_dry_run_with_merged_side_branch_lists_only_first_parent_commits(
    merged_side_branch_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Regression: the push preview must not list side-branch commits.

    Only the two first-parent commits (local work + merge commit) should
    appear in the 'About to push commits:' output.
    """
    repo = merged_side_branch_repo
    code, out, err = run_cli(repo, gpush_main, ["--dry-run", "--all"], monkeypatch)
    assert code == 0, (out, err)
    assert "local work" in out
    assert "Merge side branch" in out
    assert "side commit 1" not in out
    assert "side commit 2" not in out


def test_gpush_follow_merges_flag_lists_side_branch_commits(
    merged_side_branch_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--follow-merges`` must re-include side-branch commits in the push preview."""
    repo = merged_side_branch_repo
    code, out, err = run_cli(repo, gpush_main, ["--dry-run", "--all", "--follow-merges"], monkeypatch)
    assert code == 0, (out, err)
    assert "side commit 1" in out
    assert "side commit 2" in out


def _add_self_origin_and_fetch(repo: Path) -> None:
    if git("remote", "get-url", "origin", cwd=repo, check=False).returncode != 0:
        git("remote", "add", "origin", str(repo.resolve()), cwd=repo)
    git("fetch", "origin", cwd=repo)
    git("config", "ger.project", "testproj", cwd=repo, check=False)


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


def test_gpush_warn_not_rebased_when_remote_ahead(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _add_self_origin_and_fetch(stack_repo)
    _advance_main_with_commit(stack_repo)
    git("config", "ger.pushRemotePolicy", "warn-not-rebased", cwd=stack_repo)
    code, out, err = run_cli(stack_repo, gpush_main, ["--dry-run"], monkeypatch)
    assert code == 0
    assert "About to push commits:" in out
    assert "warning:" in err.lower()
    assert "ger rebase --onto-remote" in err


def test_gpush_error_not_rebased_exits(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _add_self_origin_and_fetch(stack_repo)
    _advance_main_with_commit(stack_repo)
    git("config", "ger.pushRemotePolicy", "error-not-rebased", cwd=stack_repo)
    code, _out, err = run_cli(stack_repo, gpush_main, ["--dry-run"], monkeypatch)
    assert code == 1
    assert "error:" in err.lower()
    assert "ger rebase --onto-remote" in err


def test_gpush_warn_policy_skips_when_fetch_impossible(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from gerrit_workflow_tools.core.git_run import GitError

    def git_fetch_fail(*args, **kwargs):
        if args and args[0] == "fetch":
            raise GitError("git fetch origin failed: simulated fetch failure")
        return git(*args, **kwargs)

    monkeypatch.setattr("gerrit_workflow_tools.core.push_checks.git", git_fetch_fail)
    git("config", "ger.pushRemotePolicy", "warn-not-rebased", cwd=stack_repo)
    code, out, err = run_cli(stack_repo, gpush_main, ["--dry-run"], monkeypatch)
    assert code == 0, err
    assert "About to push commits:" in out
    assert "skipping remote rebase check" in err.lower()


def test_gpush_ready_is_the_way_back_from_wip(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, _err = run_cli(stack_repo, gpush_main, ["--dry-run", "--ready"], monkeypatch)
    assert code == 0
    assert ":refs/for/main%ready" in out

    with pytest.raises(SystemExit) as caught:
        run_cli(stack_repo, gpush_main, ["--dry-run", "--ready", "--wip"], monkeypatch)
    assert caught.value.code == ExitCode.USAGE


def test_gpush_no_private_is_the_way_back_from_private(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, _err = run_cli(stack_repo, gpush_main, ["--dry-run", "--no-private"], monkeypatch)
    assert code == 0
    assert ":refs/for/main%remove-private" in out
    assert "Private no (--no-private)" in re.sub(r"\[[0-9;]*m", "", out)

    with pytest.raises(SystemExit) as caught:
        run_cli(stack_repo, gpush_main, ["--dry-run", "--private", "--no-private"], monkeypatch)
    assert caught.value.code == ExitCode.USAGE


def test_gpush_no_private_reaches_private_changes_gerrit_already_has(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = commits_in_range(stack_repo, "main..feature")[:2]
    store = ChangeStore(build_details_by_change_id(rows, per_index_overrides=[{"private": True}, {}]))
    mock_run = _rejected_as_unchanged(monkeypatch)

    code, out, err = run_cli(stack_repo, gpush_main, ["-y", "--no-private", rows[1].sha], monkeypatch, gerrit=store)

    assert code == 0, err
    assert [call.args[1] for call in store.calls_to("set_private")] == [False]
    assert f"set {rows[0].short_sha}  # {rows[0].subject}  remove-private" in out
    assert rows[1].short_sha not in out.split("set on Gerrit:")[1]
    mock_run.assert_not_called()


@pytest.mark.parametrize("command", ["push", "fix", "edit", "rebase", "sha"])
def test_a_verbose_flag_that_does_nothing_is_accepted_but_not_advertised(
    command: str, stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gerrit_workflow_tools.bash_completion_generator import render_bash_completion_script
    from gerrit_workflow_tools.cli_ger import main as ger_main

    _code, out, _err = run_cli(stack_repo, ger_main, [command, "--help"], monkeypatch, catch_sys_exit=True)
    assert "--verbose" not in out
    assert "currently no effect" not in render_bash_completion_script()


def test_gpush_forgets_only_the_changes_it_sent(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """After a push the next read asks in full for the changes that got a patchset, not for the whole stack."""
    from gerrit_workflow_tools.core.gerrit.rest import LOG_QUERY_OPTIONS

    kept, sent = commits_in_range(stack_repo, "main..feature")[:2]
    details = build_details_by_change_id([kept, sent])
    stale = details[list(details)[1]]
    older = git_out("rev-parse", "main", cwd=stack_repo)  # a patchset you pushed: one this repository has
    stale["current_revision"] = older
    stale["revisions"] = {older: {"_number": 1}}
    store = ChangeStore(details)
    run_cli(stack_repo, log_main, [], monkeypatch, gerrit=store)

    mock_run = MagicMock(return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""))
    monkeypatch.setattr("gerrit_workflow_tools.core.push.run_git_push", mock_run)
    code, out, err = run_cli(stack_repo, gpush_main, ["-y"], monkeypatch, gerrit=store)
    assert code == 0, err
    assert "1 commit Gerrit already has is not sent again" in out

    before = len(store.calls)
    run_cli(stack_repo, log_main, [], monkeypatch, gerrit=store)
    full = [c for c in store.calls[before:] if c.kwargs.get("options") == list(LOG_QUERY_OPTIONS)]
    assert len(full) == 1
    assert sent.change_id in full[0].args[0] and kept.change_id not in full[0].args[0]


def test_gpush_refuses_to_replace_a_patchset_it_never_had(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A web edit (or another checkout's upload) is Gerrit's current patchset: pushing over it would drop it."""
    rows = commits_in_range(stack_repo, "main..feature")
    elsewhere = "e" * 40
    details = build_details_by_change_id(rows)
    first = details[next(iter(details))]
    first["current_revision"], first["revisions"] = elsewhere, {elsewhere: {"_number": 2}}
    code, out, err = run_cli(stack_repo, gpush_main, ["-y"], monkeypatch, gerrit=ChangeStore(details))
    assert code == 1
    assert f"{rows[0].short_sha} would replace patchset 2 of change 100" in err
    assert "`ger checkout change:100 --update`" in err and "--force" in err
    assert "About to push" not in out

    code, out, _err = run_cli(
        stack_repo, gpush_main, ["--dry-run", "--force"], monkeypatch, gerrit=ChangeStore(details)
    )
    assert code == 0 and "About to push" in out


def test_glog_names_a_diverged_commit(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = commits_in_range(stack_repo, "main..feature")
    elsewhere = "e" * 40
    details = build_details_by_change_id(rows)
    first = details[next(iter(details))]
    first["current_revision"], first["revisions"] = elsewhere, {elsewhere: {"_number": 2}}
    _code, out, _err = run_cli(stack_repo, log_main, [], monkeypatch, gerrit=ChangeStore(details))
    line = next(line for line in out.splitlines() if line.startswith(rows[0].short_sha))
    assert "diverged: Gerrit has a patchset you never had" in line
    assert "ahead of gerrit" not in line


def _push_calls(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock_run = MagicMock(return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""))
    monkeypatch.setattr("gerrit_workflow_tools.core.push.run_git_push", mock_run)
    return mock_run


def test_gpush_all_sends_what_the_ready_boundary_held_back_as_wip(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--all`` crosses the boundary on purpose: the part above it goes up, as work-in-progress."""
    rows = commits_in_range(stack_repo, "main..feature")
    mock_run = _push_calls(monkeypatch)
    code, _out, err = run_cli(stack_repo, gpush_main, ["-y", "--all"], monkeypatch)
    assert code == 0, err
    ready, wip = (call.args[0][-1] for call in mock_run.call_args_list)
    assert ready == f"{rows[1].sha}:refs/for/main"
    assert wip.startswith(f"{rows[3].sha}:refs/for/main%") and "wip" in wip.split("%")[1]


def test_gpush_wip_from_marks_only_the_top(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = commits_in_range(stack_repo, "main..feature")
    mock_run = _push_calls(monkeypatch)
    code, out, err = run_cli(stack_repo, gpush_main, ["-y", "--wip-from", rows[1].sha, "--topic", "t"], monkeypatch)
    assert code == 0, err
    ready, wip = (call.args[0][-1] for call in mock_run.call_args_list)
    assert ready == f"{rows[0].sha}:refs/for/main%topic=t"
    assert wip.startswith(f"{rows[1].sha}:refs/for/main%") and {"wip", "topic=t"} <= set(wip.split("%")[1].split(","))
    assert f"{rows[1].short_sha}" in out and "[WIP]" in out


def test_gpush_wip_from_outside_the_push_is_a_usage_error(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = commits_in_range(stack_repo, "main..feature")
    _push_calls(monkeypatch)
    code, _out, err = run_cli(stack_repo, gpush_main, ["-y", "--wip-from", rows[3].sha], monkeypatch)
    assert code == ExitCode.USAGE
    assert "is not among the commits this push sends" in err


def test_ger_assume_yes_stands_in_for_y_without_a_terminal(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An agent sandbox sets GER_ASSUME_YES=1 once instead of retrying every push with -y."""
    mock_run = _push_calls(monkeypatch)
    monkeypatch.setattr(sys, "stdin", _StdinNonTTY())
    monkeypatch.setenv("GER_ASSUME_YES", "1")
    code, _out, err = run_cli(stack_repo, gpush_main, [], monkeypatch)
    assert code == 0, err
    assert mock_run.call_count == 1


def _web_edited(rows: list[Commit], *, keep_local_patchset: bool) -> dict[str, dict[str, Any]]:
    """Every change with a patchset 2 made on Gerrit; the local commit its patchset 1, or no patchset at all."""
    details = build_details_by_change_id(rows)
    for row, change in zip(rows, details.values(), strict=True):
        web_edit(change, row.sha, keep_local_patchset=keep_local_patchset)
    return details


def test_gpush_sends_nothing_where_glog_says_outdated(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Gerrit has each local commit as an older patchset (a web edit came after): Gerrit keeps its newer ones."""
    details = _web_edited(commits_in_range(stack_repo, "main..feature"), keep_local_patchset=True)
    _code, out, _err = run_cli(stack_repo, log_main, ["--json"], monkeypatch, gerrit=ChangeStore(details))
    assert {c["patchset_status"] for c in json.loads(out)["commits"]} == {"outdated"}

    mock_run = MagicMock()
    monkeypatch.setattr("gerrit_workflow_tools.core.push.run_git_push", mock_run)
    code, out, err = run_cli(stack_repo, gpush_main, [], monkeypatch, gerrit=ChangeStore(details))
    assert (code, err) == (0, "")
    assert "nothing to push: Gerrit already has these commits" in out
    rows = commits_in_range(stack_repo, "main..feature")[:2]
    for number, row in enumerate(rows, start=100):
        assert _outdated_line(row, number) in out
    mock_run.assert_not_called()

    code, dry, err = run_cli(stack_repo, gpush_main, ["--dry-run"], monkeypatch, gerrit=ChangeStore(details))
    assert code == 0
    assert "nothing to push: Gerrit already has these commits" in dry
    assert all(_outdated_line(row, number) in dry for number, row in enumerate(rows, start=100))
    assert "[dry-run] no git push" not in err  # "nothing to push" said it already
    mock_run.assert_not_called()


def test_gpush_guards_where_glog_says_diverged(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = commits_in_range(stack_repo, "main..feature")
    details = _web_edited(rows, keep_local_patchset=False)
    _code, out, _err = run_cli(stack_repo, log_main, ["--json"], monkeypatch, gerrit=ChangeStore(details))
    shown = json.loads(out)["commits"]
    assert {c["patchset_status"] for c in shown} == {"newer"}
    assert all("diverged" in c["attention_reasons"] for c in shown)

    code, _out, err = run_cli(stack_repo, gpush_main, ["-y"], monkeypatch, gerrit=ChangeStore(details))
    assert code == 1
    assert f"{rows[0].short_sha} would replace patchset 2 of change 100" in err and "--force" in err


def _outdated_line(row: Commit, number: int) -> str:
    take_it = f"`ger checkout change:{number} --update`"
    return f"{row.short_sha} outdated: Gerrit keeps patchset 2 of change {number} ({take_it})"


def _bottom_web_edited(stack_repo: Path) -> tuple[list[Commit], dict[str, dict[str, Any]]]:
    """The ready prefix on Gerrit at older patchsets, the bottom one then edited on the web (outdated)."""
    rows = commits_in_range(stack_repo, "main..feature")[:2]
    details = at_older_patchsets(build_details_by_change_id(rows), stack_repo)
    web_edit(next(iter(details.values())), rows[0].sha)
    return rows, details


def test_gpush_refuses_to_build_on_an_outdated_patchset(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The top would go up on patchset 1 of the bottom change: not current, and without the web edit."""
    rows, details = _bottom_web_edited(stack_repo)
    _code, out, _err = run_cli(stack_repo, log_main, ["--json"], monkeypatch, gerrit=ChangeStore(details))
    assert [c["patchset_status"] for c in json.loads(out)["commits"]][:2] == ["outdated", "newer"]

    mock_run = MagicMock()
    monkeypatch.setattr("gerrit_workflow_tools.core.push.run_git_push", mock_run)
    code, _out, err = run_cli(stack_repo, gpush_main, ["-y"], monkeypatch, gerrit=ChangeStore(details))
    assert code == 1  # as the diverged refusal
    assert (
        f"error: {rows[1].short_sha} would be built on patchset 1 of change 100; Gerrit has patchset 2. "
        "Take it first (`ger checkout change:100 --update`), or push anyway with --force"
    ) in err
    mock_run.assert_not_called()


def test_gpush_force_builds_on_an_outdated_patchset(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows, details = _bottom_web_edited(stack_repo)
    mock_run = MagicMock(return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""))
    monkeypatch.setattr("gerrit_workflow_tools.core.push.run_git_push", mock_run)
    code, out, err = run_cli(stack_repo, gpush_main, ["-y", "--force"], monkeypatch, gerrit=ChangeStore(details))
    assert code == 0, err
    assert mock_run.call_count == 1
    assert mock_run.call_args[0][0][-1].startswith(f"{rows[1].sha}:refs/for/")
    assert _outdated_line(rows[0], 100) in out


def test_gpush_options_on_outdated_changes_name_the_newer_patchsets(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not a credentials problem: Gerrit has newer patchsets there, and no option reaches them over REST either."""
    details = _web_edited(commits_in_range(stack_repo, "main..feature"), keep_local_patchset=True)
    store = ChangeStore(details)
    argv = ["-y", "--topic", "t", "--wip", "--reviewers", "alice", "--reviewer-strategy", "overwrite"]
    code, _out, err = run_cli(stack_repo, gpush_main, argv, monkeypatch, gerrit=store)
    assert code == 0, err
    assert "push options not applied" in err and "Gerrit has newer patchsets of these changes" in err
    assert "`ger checkout change:100 --update`" in err and "ger.token" not in err
    writes = ("set_wip", "set_topic", "set_private", "set_reviewers_batch", "delete_reviewer")
    assert not any(store.calls_to(method) for method in writes)


@pytest.mark.parametrize("argv", [["-y"], ["--dry-run"], []])
def test_gpush_names_the_ready_boundary_on_every_path(
    argv: list[str], stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before, a real push with nothing new said only "nothing to push"; --dry-run named the boundary."""
    rows = commits_in_range(stack_repo, "main..feature")
    mock_run = _rejected_as_unchanged(monkeypatch)
    monkeypatch.setattr(sys, "stdin", _StdinNonTTY())
    code, out, _err = run_cli(
        stack_repo, gpush_main, argv, monkeypatch, gerrit=ChangeStore(build_details_by_change_id(rows[:2]))
    )
    assert code == ExitCode.OK
    boundary = rows[2]
    lines = out.splitlines()
    assert lines[:3] == [
        f'Stopped at commit "{boundary.short_sha}  # {boundary.subject}", because it matches the stop pattern '
        "`^(?:dropme!|todo\\b|test!|wip\\b)`.",
        f"... 2 commits from {boundary.short_sha} up held back by the ready boundary; "
        "`ger push --all` sends them as work-in-progress",
        "nothing to push: Gerrit already has these commits",
    ]
    mock_run.assert_not_called()


@pytest.mark.parametrize(
    ("footer", "fix"),
    [
        ("", "(`git commit --amend` at HEAD) adds one, with Gerrit's commit-msg hook installed"),
        ("\n\nChange-Id: Inot-a-valid-id", "and delete its Change-Id line; Gerrit's commit-msg hook adds a new one"),
    ],
)
def test_gpush_a_change_id_boundary_names_the_fix(
    footer: str, fix: str, stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--all does not pass a Change-Id error, so the notice names what does."""
    git("commit", "--allow-empty", "-m", f"no good footer{footer}", cwd=stack_repo)
    short = git("rev-parse", "--short", "HEAD", cwd=stack_repo).stdout.strip()
    code, out, _err = run_cli(stack_repo, gpush_main, ["--dry-run", "--all"], monkeypatch)
    assert code == 0
    assert f"... 1 commit held back by the ready boundary; `ger reword {short}` {fix}" in out


def test_gpush_wip_from_above_the_ready_boundary_names_it_and_all(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, _out, err = run_cli(stack_repo, gpush_main, ["--dry-run", "--wip-from", "HEAD"], monkeypatch)
    boundary = commits_in_range(stack_repo, "main..feature")[2]
    assert code == ExitCode.USAGE
    assert f"is not among the commits this push sends: it is above the ready boundary ({boundary.short_sha}: " in err
    assert err.rstrip().endswith("; add --all to push past it")


@pytest.mark.parametrize("topic", ["", "  "])
def test_gpush_empty_topic_is_refused_naming_the_way(
    topic: str, stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before, `--topic ""` said "nothing to push" and the topic stayed as it was."""
    mock_run = MagicMock()
    monkeypatch.setattr("gerrit_workflow_tools.core.push.run_git_push", mock_run)
    code, _out, err = run_cli(stack_repo, gpush_main, ["-y", "--topic", topic], monkeypatch)
    assert code == ExitCode.USAGE
    assert "--topic needs a name: a push cannot clear a change's topic (do that in the web UI)" in err
    mock_run.assert_not_called()


def test_gpush_usage_brackets_the_exclusive_wip_flags(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        gpush_main(["--help"])
    usage = " ".join(capsys.readouterr().out.split("positional arguments")[0].split())
    assert "[--wip | --ready | --wip-from REV] [--private | --no-private]" in usage


def test_gpush_dry_run_says_what_it_shows_before_what_it_would_not_do(stack_repo: Path) -> None:
    """Both streams into one pipe: the [dry-run] lines on stderr used to come before the preview on stdout."""
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from gerrit_workflow_tools.cli_push import main; sys.exit(main())",
            "--dry-run",
        ],
        cwd=stack_repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout
    assert proc.stdout.index("About to push commits:") < proc.stdout.index("[dry-run] not executing push")
