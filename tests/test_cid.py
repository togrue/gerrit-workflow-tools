# Spec: docu/spec/commands/sha-change-id.md
# Covers: change-id lookup, duplicate detection, --fix, malformed footer

from __future__ import annotations

import io
import logging
import sys
from pathlib import Path

from gerrit_workflow_tools.cli_changeid import (
    CHANGE_ID_RE,
    _parse_sha_body_rs,
    extract_change_id_from_msg,
)
from gerrit_workflow_tools.cli_changeid import (
    main as gcid_main,
)
from gerrit_workflow_tools.core.change_id import is_change_id_token as is_change_id
from gerrit_workflow_tools.core.git_run import git, git_out
from tests.conftest import run_cli
from tests.fixtures import GCID_CLI_CHANGE_IDS, _cid

# Tip / parent Change-Ids for :func:`tests.fixtures.make_gcid_cli_repo` (newest first in ``git log`` ranges).
_HEAD_CID = GCID_CLI_CHANGE_IDS[2]
_PARENT_CID = GCID_CLI_CHANGE_IDS[1]


def _make_repo_no_change_id_footer(path: Path) -> Path:
    env = {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    path.mkdir(parents=True, exist_ok=True)
    git("init", "-b", "main", cwd=path, env=env)
    (path / "README.md").write_text("x\n", encoding="utf-8")
    git("add", "README.md", cwd=path, env=env)
    git("commit", "-m", "no change-id footer here", cwd=path, env=env)
    return path


def _make_repo_malformed_change_id_last_line(path: Path) -> Path:
    env = {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    path.mkdir(parents=True, exist_ok=True)
    git("init", "-b", "main", cwd=path, env=env)
    (path / "README.md").write_text("x\n", encoding="utf-8")
    git("add", "README.md", cwd=path, env=env)
    git(
        "commit",
        "-m",
        "subject\n\nChange-Id: Ibad",
        cwd=path,
        env=env,
    )
    return path


def _make_repo_change_id_not_last_line(path: Path) -> Path:
    env = {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    path.mkdir(parents=True, exist_ok=True)
    git("init", "-b", "main", cwd=path, env=env)
    (path / "README.md").write_text("x\n", encoding="utf-8")
    git("add", "README.md", cwd=path, env=env)
    cid = "I" + "a" * 40
    git(
        "commit",
        "-m",
        f"subject\n\nChange-Id: {cid}\n\nSigned-off-by: test@example.com",
        cwd=path,
        env=env,
    )
    return path


def _make_stack_repo_for_fix(path: Path) -> Path:
    env = {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    path.mkdir(parents=True, exist_ok=True)
    git("init", "-b", "main", cwd=path, env=env)
    (path / "README.md").write_text("base\n", encoding="utf-8")
    git("add", "README.md", cwd=path, env=env)
    git("commit", "-m", "base", cwd=path, env=env)

    git("checkout", "-b", "feature", cwd=path, env=env)

    (path / "a.txt").write_text("a\n", encoding="utf-8")
    git("add", "a.txt", cwd=path, env=env)
    git("commit", "-m", "first commit without footer", cwd=path, env=env)

    (path / "b.txt").write_text("b\n", encoding="utf-8")
    git("add", "b.txt", cwd=path, env=env)
    git(
        "commit",
        "-m",
        "second commit footer not last\n\nChange-Id: I" + "e" * 40 + "\n\nSigned-off-by: test@example.com",
        cwd=path,
        env=env,
    )

    keep = "I" + "f" * 40
    (path / "c.txt").write_text("c\n", encoding="utf-8")
    git("add", "c.txt", cwd=path, env=env)
    git("commit", "-m", f"third commit keep\n\nChange-Id: {keep}", cwd=path, env=env)
    git("branch", "--set-upstream-to", "main", "feature", cwd=path, env=env, check=False)
    return path


def _stack_bodies(repo: Path) -> list[str]:
    raw = git_out("log", "--reverse", "main..HEAD", "--format=%B%x1f", cwd=repo)
    return [chunk for chunk in raw.split("\x1f") if chunk.strip()]


# --- Pure helpers ---


def test_is_change_id_accepts_gerrit_form():
    assert is_change_id("I" + "a" * 40)
    assert is_change_id("I" + "f" * 40)


def test_is_change_id_rejects_wrong_length_or_charset():
    assert not is_change_id("I" + "a" * 39)
    assert not is_change_id("I" + "a" * 41)
    assert not is_change_id("I" + "A" * 40)
    assert not is_change_id("x" + "a" * 40)


def test_extract_change_id_from_msg_last_line_only():
    cid = "I" + "b" * 40
    assert extract_change_id_from_msg(f"title\n\nChange-Id: {cid}\n") == cid
    assert extract_change_id_from_msg(f"title\n\nChange-Id: {cid}") == cid


def test_extract_change_id_from_msg_not_on_last_line():
    cid = "I" + "c" * 40
    msg = f"title\n\nChange-Id: {cid}\n\nSigned-off-by: x\n"
    assert extract_change_id_from_msg(msg) is None


def test_extract_change_id_from_msg_missing():
    assert extract_change_id_from_msg("only a subject") is None


def test_parse_sha_body_rs_trailing_rs_stripped():
    raw = "aaa\x1ebody1\x1ebbb\x1ebody2\x1e\x1e"
    pairs = _parse_sha_body_rs(raw)
    assert pairs == [("aaa", "body1"), ("bbb", "body2")]


def test_change_id_regex_full_line():
    m = CHANGE_ID_RE.match("Change-Id: I" + "d" * 40)
    assert m is not None
    assert m.group(1) == "I" + "d" * 40


def test_gcid_help(gcid_cli_repo, monkeypatch):
    code, out, _err = run_cli(gcid_cli_repo, gcid_main, ["--help"], monkeypatch, catch_sys_exit=True)
    assert code == 0
    assert "REV_OR_RANGE" in out


# --- CLI: synthetic three-commit repo (``gcid_cli_repo``) ---


def test_gcid_defaults_to_head(gcid_cli_repo, monkeypatch):
    code, out, err = run_cli(gcid_cli_repo, gcid_main, [], monkeypatch)
    assert code == 0
    assert err == ""
    assert out.strip() == _HEAD_CID


def test_gcid_explicit_sha(gcid_cli_repo, monkeypatch):
    head_sha = git_out("rev-parse", "HEAD", cwd=gcid_cli_repo)
    code, out, err = run_cli(gcid_cli_repo, gcid_main, [head_sha], monkeypatch)
    assert code == 0
    assert out.strip() == _HEAD_CID
    assert err == ""


def test_gcid_range_two_commits_order(gcid_cli_repo, monkeypatch):
    code, out, err = run_cli(
        gcid_cli_repo,
        gcid_main,
        ["HEAD~2..HEAD"],
        monkeypatch,
    )
    assert code == 0
    assert err == ""
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert lines == [_HEAD_CID, _PARENT_CID]


def test_gcid_single_commit_range_syntax(gcid_cli_repo, monkeypatch):
    """HEAD~1..HEAD is one commit; still uses range mode (.. present)."""
    code, out, _err = run_cli(
        gcid_cli_repo,
        gcid_main,
        ["HEAD~1..HEAD"],
        monkeypatch,
    )
    assert code == 0
    assert out.strip() == _HEAD_CID


def test_gcid_passthrough_change_id_no_git(gcid_cli_repo, monkeypatch):
    code, out, err = run_cli(gcid_cli_repo, gcid_main, [_HEAD_CID], monkeypatch)
    assert code == 0
    assert out.strip() == _HEAD_CID
    assert err == ""


def test_gcid_invalid_ref(gcid_cli_repo, monkeypatch):
    code, out, err = run_cli(
        gcid_cli_repo,
        gcid_main,
        ["not-a-valid-ref-99999999"],
        monkeypatch,
    )
    assert code == 1
    assert out == ""
    assert "git" in err.lower() or "unknown" in err.lower() or err.strip()


def test_gcid_verbose(gcid_cli_repo, monkeypatch):
    code, out, _err = run_cli(gcid_cli_repo, gcid_main, ["--debug-log", "HEAD"], monkeypatch)
    assert code == 0
    assert _HEAD_CID in out


def test_gcid_vv_logs_git_subprocess(gcid_cli_repo, monkeypatch):
    """With --debug-log, git_run logs each subprocess at DEBUG on the package logger (propagate=False)."""
    buf = io.StringIO()
    extra = logging.StreamHandler(buf)
    extra.setLevel(logging.DEBUG)
    pkg = logging.getLogger("gerrit_workflow_tools")
    pkg.addHandler(extra)
    pkg.setLevel(logging.DEBUG)
    try:
        monkeypatch.chdir(gcid_cli_repo)
        out_buf = io.StringIO()
        monkeypatch.setattr(sys, "stdout", out_buf)
        monkeypatch.setattr(sys, "stderr", io.StringIO())
        code = gcid_main(["--debug-log", "HEAD"])
    finally:
        pkg.removeHandler(extra)
    assert code == 0
    assert _HEAD_CID in out_buf.getvalue()
    assert "run: git" in buf.getvalue()


# --- CLI: --start-at-remote (stack_repo: main + feature with 4 commits) ---


def test_gcid_start_at_remote_lists_stack_newest_first(stack_repo, monkeypatch):
    code, out, err = run_cli(stack_repo, gcid_main, ["--start-at-remote"], monkeypatch)
    assert code == 0
    assert err == ""
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    assert lines == [_cid("4"), _cid("3"), _cid("2"), _cid("1")]


def test_gcid_start_at_remote_end_ref(stack_repo, monkeypatch):
    code, out, err = run_cli(stack_repo, gcid_main, ["--start-at-remote", "HEAD~2"], monkeypatch)
    assert code == 0
    assert err == ""
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    assert lines == [_cid("2"), _cid("1")]


def test_gcid_start_at_remote_range_ignores_left_endpoint(stack_repo, monkeypatch):
    """``--start-at-remote`` uses upstream_tip..RIGHT (same stack window as default `ger log`)."""
    full = run_cli(stack_repo, gcid_main, ["--start-at-remote"], monkeypatch)
    ranged = run_cli(stack_repo, gcid_main, ["--start-at-remote", "HEAD~3..HEAD"], monkeypatch)
    assert full[0] == 0 and ranged[0] == 0
    assert full[1] == ranged[1]


def test_gcid_start_at_remote_change_id_passthrough(stack_repo, monkeypatch):
    cid = _cid("4")
    code, out, err = run_cli(stack_repo, gcid_main, ["--start-at-remote", cid], monkeypatch)
    assert code == 0
    assert err == ""
    assert out.strip() == cid


# --- CLI: --check-duplicates ---


def test_gcid_check_duplicates_ok(stack_repo, monkeypatch):
    code, out, err = run_cli(stack_repo, gcid_main, ["--check-duplicates"], monkeypatch)
    assert code == 0
    assert out == ""
    assert err == ""


def test_gcid_check_duplicates_fails_on_dup(dup_repo, monkeypatch):
    code, out, err = run_cli(dup_repo, gcid_main, ["--check-duplicates"], monkeypatch)
    assert code == 2
    assert out == ""
    assert "duplicate" in err.lower()


def test_gcid_check_duplicates_rejects_change_id_arg(stack_repo, monkeypatch):
    cid = _cid("4")
    code, out, err = run_cli(stack_repo, gcid_main, ["--check-duplicates", cid], monkeypatch)
    assert code == 2
    assert out == ""
    assert "change-id" in err.lower() or "Change-Id" in err


def test_gcid_check_duplicates_end_ref(stack_repo, monkeypatch):
    code, out, err = run_cli(stack_repo, gcid_main, ["--check-duplicates", "HEAD~2"], monkeypatch)
    assert code == 0
    assert out == ""
    assert err == ""


# --- CLI: synthetic repos ---


def test_gcid_missing_change_id_exits_1(tmp_path, monkeypatch):
    repo = _make_repo_no_change_id_footer(tmp_path / "r")
    code, out, err = run_cli(repo, gcid_main, ["HEAD"], monkeypatch)
    assert code == 1
    assert out == ""
    assert "no Change-Id" in err


def test_gcid_change_id_not_last_line_exits_1(tmp_path, monkeypatch):
    repo = _make_repo_change_id_not_last_line(tmp_path / "r2")
    code, _out, err = run_cli(repo, gcid_main, ["HEAD"], monkeypatch)
    assert code == 1
    assert "no Change-Id" in err


def test_gcid_malformed_change_id_last_line_exits_1(tmp_path, monkeypatch):
    repo = _make_repo_malformed_change_id_last_line(tmp_path / "r3")
    code, _out, err = run_cli(repo, gcid_main, ["HEAD"], monkeypatch)
    assert code == 1
    assert "no Change-Id" in err


def test_gcid_string_that_is_not_change_id_tries_git(gcid_cli_repo, monkeypatch):
    """Too-short I… is not passthrough; git log fails for unknown object."""
    bad = "I" + "a" * 39
    code, out, _err = run_cli(gcid_cli_repo, gcid_main, [bad], monkeypatch)
    assert code == 1
    assert out == ""


def test_gcid_fix_assigns_missing_and_not_last_line(tmp_path, monkeypatch):
    repo = _make_stack_repo_for_fix(tmp_path / "fix")
    code, out, err = run_cli(repo, gcid_main, ["--fix"], monkeypatch)
    assert code == 0
    assert out == ""
    assert err == ""

    bodies = _stack_bodies(repo)
    assert len(bodies) == 3
    for body in bodies:
        assert extract_change_id_from_msg(body) is not None

    second = bodies[1]
    assert "Signed-off-by: test@example.com" in second
    assert second.count("Change-Id:") == 1

    check_code, check_out, check_err = run_cli(repo, gcid_main, ["--check-duplicates"], monkeypatch)
    assert check_code == 0
    assert check_out == ""
    assert check_err == ""


def test_gcid_fix_skips_commit_with_valid_last_line_change_id(tmp_path, monkeypatch):
    repo = _make_stack_repo_for_fix(tmp_path / "fix_skip")
    before = _stack_bodies(repo)
    assert "Change-Id: I" + "f" * 40 in before[2]

    code, out, err = run_cli(repo, gcid_main, ["--fix"], monkeypatch)
    assert code == 0
    assert out == ""
    assert err == ""

    after = _stack_bodies(repo)
    assert "Change-Id: I" + "f" * 40 in after[2]
    assert after[2].count("Change-Id:") == 1


def test_gcid_fix_rejects_check_duplicates_combo(stack_repo, monkeypatch):
    code, out, err = run_cli(stack_repo, gcid_main, ["--fix", "--check-duplicates"], monkeypatch)
    assert code == 2
    assert out == ""
    assert "cannot be combined" in err


def test_gcid_fix_requires_clean_tree(stack_repo, monkeypatch):
    (stack_repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    code, out, err = run_cli(stack_repo, gcid_main, ["--fix"], monkeypatch)
    assert code == 2
    assert out == ""
    assert "clean working tree" in err
