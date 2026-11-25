# Spec: docu/spec/commands/show.md
# Covers: HEAD query, --json, --verbose, warning highlighting, invalid change number

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_show import main as gshow_main
from gerrit_workflow_tools.core.gerrit.rest import LOG_QUERY_OPTIONS
from gerrit_workflow_tools.core.git_run import git, git_out
from gerrit_workflow_tools.render.style import ANSI_YELLOW, GERRIT_LINK_LABEL, strip_ansi
from tests.change_store import ChangeStore
from tests.cli_gerrit_mocks import (
    build_details_by_change_id,
    change_info_for_sha,
    head_change_id,
    stack_rows_mb_to_head,
)
from tests.conftest import json_stdout, run_cli
from tests.helpers import force_zero_change_trust_window, write_rebase_head


class _StdinTTY:
    def isatty(self) -> bool:
        return True


def _detail_ok(
    *,
    change_id: str,
    sha: str,
    cr_value: int = 2,
    v_value: int = 1,
    number: int = 99,
    project: str = "testproj",
    branch: str = "main",
) -> dict:
    """Minimal ChangeInfo for :func:`fetch_gerrit_data`."""
    return {
        "id": f"{project}~{branch}~{change_id}",
        "change_id": change_id,
        "project": project,
        "branch": branch,
        "_number": number,
        "subject": "subj",
        "current_revision": sha,
        "submittable": True,
        "unresolved_comment_count": 0,
        "revisions": {sha: {"_number": 1}},
        "labels": {
            "Verified": {"value": v_value, "all": [{"value": v_value}]},
            "Code-Review": {"value": cr_value, "all": [{"value": cr_value}]},
        },
        "reviewers": [{"account": {"username": "default-reviewer"}, "state": "REVIEWER"}],
    }


def test_gshow_accepts_git_range(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ranges expand to local commits; multi-commit ranges wrap JSON in commits[]."""
    # Stack is upstream..HEAD with multiple commits; use main..HEAD for 2+.
    tip = git_out("rev-parse", "HEAD", cwd=web_stack_repo)
    store = ChangeStore({})
    code, out, err = run_cli(
        web_stack_repo,
        gshow_main,
        ["--json", "main..HEAD"],
        monkeypatch,
        gerrit=store,
    )
    assert code in (0, 1), err
    data = json_stdout(out)
    assert "commits" in data
    assert len(data["commits"]) >= 2
    assert data["commits"][-1]["sha"] == tip


def test_gshow_stack_json(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, err = run_cli(
        web_stack_repo,
        gshow_main,
        ["--json", "--stack"],
        monkeypatch,
        gerrit=ChangeStore({}),
    )
    assert code in (0, 1), err
    data = json_stdout(out)
    assert "commits" in data
    assert len(data["commits"]) >= 2


def test_gshow_stack_missing_upstream_non_tty_prints_setup_hint(
    web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git("branch", "--unset-upstream", cwd=web_stack_repo, check=False)
    code, out, err = run_cli(
        web_stack_repo,
        gshow_main,
        ["--stack"],
        monkeypatch,
        gerrit=ChangeStore({}),
    )
    assert code == ExitCode.CONFIG
    assert out == ""
    assert "No upstream configured for branch" in err
    assert "git branch --set-upstream-to=" in err
    assert "git log failed" not in err


def test_gshow_stack_prompts_for_missing_upstream_and_aborts(
    web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git("branch", "--unset-upstream", "feature", cwd=web_stack_repo, check=False)
    monkeypatch.setattr(sys, "stdin", _StdinTTY())
    monkeypatch.setattr(
        "gerrit_workflow_tools.upstream_interactive.prompt_upstream_abbrev_interactive",
        lambda _cwd, _branch, **_kw: None,
    )
    code, out, _err = run_cli(
        web_stack_repo,
        gshow_main,
        ["--stack"],
        monkeypatch,
        gerrit=ChangeStore({}),
    )
    assert code == ExitCode.REFUSED
    assert out == ""
    p = git("rev-parse", "--abbrev-ref", "feature@{upstream}", cwd=web_stack_repo, check=False)
    assert p.returncode != 0


def test_gshow_stack_sets_missing_upstream_then_continues(
    web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git("branch", "--unset-upstream", "feature", cwd=web_stack_repo, check=False)
    monkeypatch.setattr(sys, "stdin", _StdinTTY())
    monkeypatch.setattr(
        "gerrit_workflow_tools.upstream_interactive.prompt_upstream_abbrev_interactive",
        lambda _cwd, _branch, **_kw: "origin/main",
    )
    code, _out, err = run_cli(
        web_stack_repo,
        gshow_main,
        ["--json", "--stack"],
        monkeypatch,
        gerrit=ChangeStore({}),
    )
    assert code in (0, 1), err
    assert "Upstream for 'feature' set to origin/main." in err
    assert git_out("rev-parse", "--abbrev-ref", "feature@{upstream}", cwd=web_stack_repo) == "origin/main"


def test_gshow_head_asks_for_missing_upstream_then_shows(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Showing HEAD needs the stack's destination branch: on a terminal ger asks for the upstream and goes on."""
    git("branch", "--unset-upstream", "feature", cwd=web_stack_repo, check=False)
    monkeypatch.setattr(sys, "stdin", _StdinTTY())
    monkeypatch.setattr(
        "gerrit_workflow_tools.upstream_interactive.prompt_upstream_abbrev_interactive",
        lambda _cwd, _branch, **_kw: "origin/main",
    )
    sha = git_out("rev-parse", "HEAD", cwd=web_stack_repo)
    detail = change_info_for_sha(sha, head_change_id(web_stack_repo), number=77)
    code, out, err = run_cli(
        web_stack_repo, gshow_main, ["HEAD"], monkeypatch, gerrit=ChangeStore({str(detail["id"]): detail})
    )
    assert code == ExitCode.OK, err
    assert "Upstream for 'feature' set to origin/main." in err
    assert "77" in out


def test_gshow_markdown_ai_format(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cid = "Ibbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    sha = "abc12345678901234567890123456789012345678"
    ch = _detail_ok(change_id=cid, sha=sha, cr_value=2, number=42)
    ch["unresolved_comment_count"] = 1
    comments = {
        "epsilon.txt": [
            {
                "id": "root-id",
                "line": 908,
                "message": "please fix",
                "unresolved": True,
                "updated": "2024-01-01 10:00:00",
                "author": {"username": "alice", "name": "Alice"},
            },
            {
                "id": "reply-id",
                "line": 908,
                "message": "looking",
                "unresolved": True,
                "in_reply_to": "root-id",
                "updated": "2024-01-01 11:00:00",
                "author": {"username": "bob", "name": "Bob"},
            },
        ]
    }
    store = ChangeStore({str(ch["id"]): ch}, web_base="https://g.example")
    store.set_comments(str(ch["id"]), comments)
    code, out, _err = run_cli(
        web_stack_repo,
        gshow_main,
        ["--ai", "change:42"],
        monkeypatch,
        gerrit=store,
    )
    assert code == 0
    assert "## " in out
    assert "### `epsilon.txt:908`" in out
    assert "**alice (Alice)**" in out or "**Alice**" in out or "alice" in out.lower()
    assert "> please fix" in out
    assert "> looking" in out
    assert "\033[" not in out


def test_gshow_multi_human_lists_clean_commits_briefly(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With multiple targets, a commit without unresolved chains gets a headline and its subject."""
    tip = git_out("rev-parse", "HEAD", cwd=web_stack_repo)
    parent = git_out("rev-parse", "HEAD~1", cwd=web_stack_repo)
    tip_cid = git_out("log", "-1", "--format=%(trailers:key=Change-Id,valueonly)", tip, cwd=web_stack_repo).strip()
    parent_cid = git_out(
        "log", "-1", "--format=%(trailers:key=Change-Id,valueonly)", parent, cwd=web_stack_repo
    ).strip()
    dirty = _detail_ok(change_id=tip_cid, sha=tip, number=201, cr_value=0, v_value=1)
    dirty["unresolved_comment_count"] = 1
    clean = _detail_ok(change_id=parent_cid, sha=parent, number=200, cr_value=2, v_value=1)
    store = ChangeStore({str(dirty["id"]): dirty, str(clean["id"]): clean}, web_base="https://g.example")
    store.set_comments(
        str(dirty["id"]),
        {
            "x.py": [
                {
                    "id": "c1",
                    "line": 2,
                    "message": "needs fix",
                    "unresolved": True,
                    "author": {"username": "alice", "name": "Alice"},
                }
            ]
        },
    )
    parent_subj = git_out("log", "-1", "--format=%s", parent, cwd=web_stack_repo)
    tip_subj = git_out("log", "-1", "--format=%s", tip, cwd=web_stack_repo)
    code, out, err = run_cli(
        web_stack_repo,
        gshow_main,
        ["--color=never", parent, tip],
        monkeypatch,
        gerrit=store,
    )
    assert code == 0, err
    assert "needs fix" in out
    assert "╭─ x.py:2" in out
    assert tip_subj in out
    assert out.count("commit ") == 2
    assert git_out("rev-parse", "--short", tip, cwd=web_stack_repo) in out
    assert git_out("rev-parse", "--short", parent, cwd=web_stack_repo) in out
    assert parent_subj in out
    assert "(no unresolved comments)" in out


def test_gshow_multi_markdown_lists_clean_commits(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With --ai/--stack, a commit without unresolved chains still gets its heading."""
    tip = git_out("rev-parse", "HEAD", cwd=web_stack_repo)
    parent = git_out("rev-parse", "HEAD~1", cwd=web_stack_repo)
    tip_cid = git_out("log", "-1", "--format=%(trailers:key=Change-Id,valueonly)", tip, cwd=web_stack_repo).strip()
    parent_cid = git_out(
        "log", "-1", "--format=%(trailers:key=Change-Id,valueonly)", parent, cwd=web_stack_repo
    ).strip()
    dirty = _detail_ok(change_id=tip_cid, sha=tip, number=201, cr_value=0, v_value=1)
    dirty["unresolved_comment_count"] = 1
    clean = _detail_ok(change_id=parent_cid, sha=parent, number=200, cr_value=2, v_value=1)
    store = ChangeStore({str(dirty["id"]): dirty, str(clean["id"]): clean}, web_base="https://g.example")
    store.set_comments(
        str(dirty["id"]),
        {
            "x.py": [
                {
                    "id": "c1",
                    "line": 2,
                    "message": "needs fix",
                    "unresolved": True,
                    "author": {"username": "alice", "name": "Alice"},
                }
            ]
        },
    )
    parent_subj = git_out("log", "-1", "--format=%s", parent, cwd=web_stack_repo)
    tip_subj = git_out("log", "-1", "--format=%s", tip, cwd=web_stack_repo)
    code, out, err = run_cli(
        web_stack_repo,
        gshow_main,
        ["--ai", "--stack"],
        monkeypatch,
        gerrit=store,
    )
    assert code == 0, err
    assert "> needs fix" in out
    assert "### `x.py:2`" in out
    assert tip_subj in out
    assert sum(1 for line in out.splitlines() if line.startswith("## ")) == 4
    assert git_out("rev-parse", "--short", tip, cwd=web_stack_repo) in out
    assert git_out("rev-parse", "--short", parent, cwd=web_stack_repo) in out
    assert parent_subj in out
    assert "\033[" not in out


def test_gshow_multi_human_comments_all_includes_resolved_only_commits(
    web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--comments all`` prints commits that only have resolved threads."""
    tip = git_out("rev-parse", "HEAD", cwd=web_stack_repo)
    parent = git_out("rev-parse", "HEAD~1", cwd=web_stack_repo)
    tip_cid = git_out("log", "-1", "--format=%(trailers:key=Change-Id,valueonly)", tip, cwd=web_stack_repo).strip()
    parent_cid = git_out(
        "log", "-1", "--format=%(trailers:key=Change-Id,valueonly)", parent, cwd=web_stack_repo
    ).strip()
    dirty = _detail_ok(change_id=tip_cid, sha=tip, number=201, cr_value=0, v_value=1)
    dirty["unresolved_comment_count"] = 1
    closed = _detail_ok(change_id=parent_cid, sha=parent, number=200, cr_value=2, v_value=1)
    store = ChangeStore({str(dirty["id"]): dirty, str(closed["id"]): closed}, web_base="https://g.example")
    store.set_comments(
        str(dirty["id"]),
        {
            "x.py": [
                {
                    "id": "c1",
                    "line": 2,
                    "message": "needs fix",
                    "unresolved": True,
                    "author": {"username": "alice", "name": "Alice"},
                }
            ]
        },
    )
    store.set_comments(str(closed["id"]), _resolved_thread())
    code, out, err = run_cli(
        web_stack_repo,
        gshow_main,
        ["--color=never", "--comments", "all", parent, tip],
        monkeypatch,
        gerrit=store,
    )
    assert code == 0, err
    assert "needs fix" in out
    assert "please fix" in out
    assert "╭─ f.py:1 (resolved)" in out
    assert out.count("commit ") == 2
    assert git_out("rev-parse", "--short", tip, cwd=web_stack_repo) in out
    assert git_out("rev-parse", "--short", parent, cwd=web_stack_repo) in out


def test_gshow_human_thread_gutter(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:

    cid = "Ibbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    sha = "abc12345678901234567890123456789012345678"
    ch = _detail_ok(change_id=cid, sha=sha, cr_value=2, number=42)
    ch["unresolved_comment_count"] = 1
    comments = {
        "f.py": [
            {
                "id": "root-id",
                "line": 1,
                "message": "please fix",
                "unresolved": True,
                "updated": "2024-01-01 10:00:00",
                "author": {"username": "alice", "name": "Alice"},
            },
            {
                "id": "reply-id",
                "line": 1,
                "message": "looking",
                "unresolved": True,
                "in_reply_to": "root-id",
                "updated": "2024-01-01 11:00:00",
                "author": {"username": "bob", "name": "Bob"},
            },
        ]
    }
    store = ChangeStore({str(ch["id"]): ch}, web_base="https://g.example")
    store.set_comments(str(ch["id"]), comments)
    code, out, _err = run_cli(
        web_stack_repo,
        gshow_main,
        ["--color=never", "change:42"],
        monkeypatch,
        gerrit=store,
    )
    assert code == 0
    assert "f.py:1" in out
    assert "╭─ f.py:1" in out
    assert "╰" in out
    assert "looking" in out
    assert "└ " not in out


def test_gshow_multi_arg_json_wraps(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tip = git_out("rev-parse", "HEAD", cwd=web_stack_repo)
    parent = git_out("rev-parse", "HEAD~1", cwd=web_stack_repo)
    code, out, err = run_cli(
        web_stack_repo,
        gshow_main,
        ["--json", parent, tip],
        monkeypatch,
        gerrit=ChangeStore({}),
    )
    assert code in (0, 1), err
    data = json_stdout(out)
    assert "commits" in data
    assert len(data["commits"]) == 2
    assert data["commits"][0]["sha"] == parent
    assert data["commits"][1]["sha"] == tip


def test_gshow_json_format_mutex(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, _out, err = run_cli(
        stack_repo,
        gshow_main,
        ["--json", "--ai"],
        monkeypatch,
        catch_sys_exit=True,
    )
    assert code == 2
    assert "not allowed" in err.lower() or "exclusive" in err.lower()


def test_gshow_json_change_id_asks_gerrit_for_current_revision(
    web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: bare ``changes/?q=`` omits ``current_revision`` unless ``o=CURRENT_REVISION``."""
    cid = "Ibbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    sha = "abc12345678901234567890123456789012345678"
    ch = _detail_ok(change_id=cid, sha=sha, cr_value=2, number=42)
    store = ChangeStore({str(ch["id"]): ch}, web_base="https://g.example")
    code, out, _err = run_cli(
        web_stack_repo,
        gshow_main,
        ["--json", cid],
        monkeypatch,
        gerrit=store,
    )
    assert code == 0
    data = json_stdout(out)
    assert data["sha"] == sha
    # The details come from one request that asks for the current revision, not from the bare lookup.
    detailed = [c for c in store.calls_to("query_changes") if c.kwargs.get("options") == list(LOG_QUERY_OPTIONS)]
    assert len(detailed) + len(store.calls_to("get_change")) == 1


def test_gshow_json_numeric_change_mocked(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cid = "Ibbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    sha = "abc12345678901234567890123456789012345678"
    ch = _detail_ok(change_id=cid, sha=sha, cr_value=2, number=42)
    ch["_number"] = 42
    store = ChangeStore({str(ch["id"]): ch}, web_base="https://g.example")
    code, out, _err = run_cli(
        web_stack_repo,
        gshow_main,
        ["--json", "change:42"],
        monkeypatch,
        gerrit=store,
    )
    assert code == 0
    data = json_stdout(out)
    assert data["change_id"] == cid
    assert data["patchset_status"] == "active"
    assert data["local_commit"] is False
    assert data["attention_reasons"] == []


def test_gshow_json_long_comment(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cid = "Ibbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    sha = "abc12345678901234567890123456789012345678"
    ch = _detail_ok(change_id=cid, sha=sha, cr_value=2, number=42)
    long_msg = "\n".join(f"line{i}" for i in range(15))
    comments = {
        "f.py": [
            {
                "id": "TvcXrmjM",
                "line": 1,
                "message": long_msg,
                "unresolved": True,
                "patch_set": 1,
            }
        ]
    }
    store = ChangeStore({str(ch["id"]): ch}, web_base="https://g.example")
    store.set_comments(str(ch["id"]), comments)
    code, out, _err = run_cli(
        web_stack_repo,
        gshow_main,
        ["--json", "change:42"],
        monkeypatch,
        gerrit=store,
    )
    assert code == 0
    data = json_stdout(out)
    c0 = data["comments"][0]
    assert c0["message"] == long_msg
    assert "line0" in c0["message"] and "line14" in c0["message"]
    assert data["comments"][0]["url"] == "https://g.example/c/testproj/+/42/comment/TvcXrmjM/"
    assert data["comment_chains"][0]["resolved"] is False
    # An agent names a chain by its root id, and a reply lands on the comment's patchset.
    assert data["comment_chains"][0]["root_id"] == "TvcXrmjM"
    assert c0["patch_set"] == 1
    assert c0["draft"] is False


def test_gshow_skips_resolved_comment_chain(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolved thread: last reply unresolved=false hides entire chain."""
    cid = "Ibbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    sha = "abc12345678901234567890123456789012345678"
    ch = _detail_ok(change_id=cid, sha=sha, cr_value=2, number=42)
    comments = {
        "f.py": [
            {
                "id": "root-id",
                "line": 1,
                "message": "please fix",
                "unresolved": True,
                "updated": "2024-01-01 10:00:00",
            },
            {
                "id": "reply-id",
                "line": 1,
                "message": "done",
                "unresolved": False,
                "in_reply_to": "root-id",
                "updated": "2024-01-01 11:00:00",
            },
        ]
    }
    store = ChangeStore({str(ch["id"]): ch}, web_base="https://g.example")
    store.set_comments(str(ch["id"]), comments)
    code, out, _err = run_cli(
        web_stack_repo,
        gshow_main,
        ["--color=never", "change:42"],
        monkeypatch,
        gerrit=store,
    )
    assert code == 0
    assert "please fix" not in out
    assert "done" not in out
    assert "╭" not in out


def _resolved_thread() -> dict[str, list[dict]]:
    return {
        "f.py": [
            {
                "id": "root-id",
                "line": 1,
                "message": "please fix",
                "unresolved": True,
                "updated": "2024-01-01 10:00:00",
                "author": {"username": "alice", "name": "Alice"},
            },
            {
                "id": "reply-id",
                "line": 1,
                "message": "done",
                "unresolved": False,
                "in_reply_to": "root-id",
                "updated": "2024-01-01 11:00:00",
                "author": {"username": "bob", "name": "Bob"},
            },
        ]
    }


def test_gshow_comments_all_prints_resolved_chain(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cid = "Ibbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    sha = "abc12345678901234567890123456789012345678"
    ch = _detail_ok(change_id=cid, sha=sha, cr_value=2, number=42)
    store = ChangeStore({str(ch["id"]): ch}, web_base="https://g.example")
    store.set_comments(str(ch["id"]), _resolved_thread())
    code, out, _err = run_cli(
        web_stack_repo,
        gshow_main,
        ["--color=never", "--comments", "all", "change:42"],
        monkeypatch,
        gerrit=store,
    )
    assert code == 0
    assert "please fix" in out
    assert "done" in out
    assert "╭─ f.py:1 (resolved)" in out


def test_gshow_comments_resolved_hides_open_threads(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cid = "Ibbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    sha = "abc12345678901234567890123456789012345678"
    ch = _detail_ok(change_id=cid, sha=sha, cr_value=2, number=42)
    ch["unresolved_comment_count"] = 1
    comments = {
        "open.py": [
            {
                "id": "open-id",
                "line": 2,
                "message": "still broken",
                "unresolved": True,
                "updated": "2024-01-01 10:00:00",
            }
        ],
        "done.py": [
            {
                "id": "done-id",
                "line": 3,
                "message": "nit: done",
                "unresolved": False,
                "updated": "2024-01-01 11:00:00",
            }
        ],
    }
    store = ChangeStore({str(ch["id"]): ch}, web_base="https://g.example")
    store.set_comments(str(ch["id"]), comments)
    code, out, err = run_cli(
        web_stack_repo,
        gshow_main,
        ["--color=never", "--comments", "resolved", "change:42"],
        monkeypatch,
        gerrit=store,
    )
    assert code == 0, err
    assert "nit: done" in out
    assert "╭─ done.py:3 (resolved)" in out
    assert "still broken" not in out


def test_gshow_comments_all_json_marks_resolved(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cid = "Ibbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    sha = "abc12345678901234567890123456789012345678"
    ch = _detail_ok(change_id=cid, sha=sha, cr_value=2, number=42)
    store = ChangeStore({str(ch["id"]): ch}, web_base="https://g.example")
    store.set_comments(str(ch["id"]), _resolved_thread())
    code, out, _err = run_cli(
        web_stack_repo,
        gshow_main,
        ["--json", "--comments", "all", "change:42"],
        monkeypatch,
        gerrit=store,
    )
    assert code == 0
    data = json_stdout(out)
    assert data["comments_unresolved"] == 0
    assert data["comment_chains"][0]["resolved"] is True
    assert [c["message"] for c in data["comments"]] == ["please fix", "done"]


def test_gshow_ai_comments_all_has_resolved_section(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cid = "Ibbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    sha = "abc12345678901234567890123456789012345678"
    ch = _detail_ok(change_id=cid, sha=sha, cr_value=2, number=42)
    store = ChangeStore({str(ch["id"]): ch}, web_base="https://g.example")
    store.set_comments(str(ch["id"]), _resolved_thread())
    code, out, _err = run_cli(
        web_stack_repo,
        gshow_main,
        ["--ai", "--comments", "all", "change:42"],
        monkeypatch,
        gerrit=store,
    )
    assert code == 0
    assert "### Resolved comments" in out
    assert "### `f.py:1` (resolved)" in out
    assert "> please fix" in out
    assert "> done" in out
    assert "### Unresolved comments" not in out


def _open_thread_with_context() -> dict[str, list[dict]]:
    return {
        "f.py": [
            {
                "id": "c1",
                "line": 2,
                "message": "please fix",
                "unresolved": True,
                "updated": "2024-01-01 10:00:00",
                "author": {"username": "alice", "name": "Alice"},
                "context_lines": [
                    {"line_number": 1, "context_line": "alpha()"},
                    {"line_number": 2, "context_line": "beta()"},
                ],
            }
        ]
    }


def test_gshow_context_padding_implies_context(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cid = "Ibbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    sha = "abc12345678901234567890123456789012345678"
    ch = _detail_ok(change_id=cid, sha=sha, cr_value=0, number=42)
    ch["unresolved_comment_count"] = 1
    store = ChangeStore({str(ch["id"]): ch}, web_base="https://g.example")
    store.set_comments(str(ch["id"]), _open_thread_with_context())
    code, out, err = run_cli(
        web_stack_repo,
        gshow_main,
        ["--color=never", "--context-padding", "0", "change:42"],
        monkeypatch,
        gerrit=store,
    )
    assert code == 0, err
    assert "   1  alpha()" in out


def test_gshow_context_json_includes_context_lines(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cid = "Ibbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    sha = "abc12345678901234567890123456789012345678"
    ch = _detail_ok(change_id=cid, sha=sha, cr_value=2, number=42)
    ch["unresolved_comment_count"] = 1
    store = ChangeStore({str(ch["id"]): ch}, web_base="https://g.example")
    store.set_comments(str(ch["id"]), _open_thread_with_context())
    code, out, _err = run_cli(
        web_stack_repo,
        gshow_main,
        ["--json", "--context", "change:42"],
        monkeypatch,
        gerrit=store,
    )
    assert code == 0
    data = json_stdout(out)
    assert data["comments"][0]["context_lines"] == [
        {"line_number": 1, "context_line": "alpha()"},
        {"line_number": 2, "context_line": "beta()"},
    ]


def test_gshow_human_shows_comment_author(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cid = "Ibbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    sha = "abc12345678901234567890123456789012345678"
    ch = _detail_ok(change_id=cid, sha=sha, cr_value=2, number=42)
    ch["unresolved_comment_count"] = 1
    comments = {
        "epsilon.txt": [
            {
                "id": "ff909dbc",
                "line": 908,
                "message": "some comment",
                "unresolved": True,
                "author": {"username": "grt", "name": "Tobias Grün"},
            }
        ]
    }
    store = ChangeStore({str(ch["id"]): ch}, web_base="https://g.example")
    store.set_comments(str(ch["id"]), comments)
    code, out, _err = run_cli(
        web_stack_repo,
        gshow_main,
        ["--color=never", "change:42"],
        monkeypatch,
        gerrit=store,
    )
    assert code == 0
    assert "epsilon.txt:908" in out
    assert "grt (Tobias Grün)" in out
    assert "some comment" in out
    assert "╭─ epsilon.txt:908" in out


def test_gshow_prints_a_control_character_in_a_file_path_escaped(
    web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A path is the file's identity, so it reaches core as Gerrit sent it; printing escapes it."""
    ch = _detail_ok(change_id="I" + "b" * 40, sha="abc12345678901234567890123456789012345678", cr_value=2, number=42)
    ch["unresolved_comment_count"] = 1
    odd = "src/a\x1b]0;pwned\x07.py"
    comment = {"id": "c1", "line": 3, "message": "look", "unresolved": True, "author": {"username": "m"}}
    store = ChangeStore({str(ch["id"]): ch}, web_base="https://g.example")
    store.set_comments(str(ch["id"]), {odd: [comment]})

    code, out, _err = run_cli(web_stack_repo, gshow_main, ["--color=never", "change:42"], monkeypatch, gerrit=store)
    assert code == 0
    assert "\x1b" not in out and "\x07" not in out
    assert "src/a\\x1b]0;pwned\\x07.py:3" in out

    code, out, _err = run_cli(web_stack_repo, gshow_main, ["--json", "change:42"], monkeypatch, gerrit=store)
    assert code == 0
    assert "\x1b" not in out  # JSON escapes it, and a client gets the real path
    assert json_stdout(out)["comments"][0]["path"] == odd


def test_gshow_json_includes_comment_author(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cid = "Ibbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    sha = "abc12345678901234567890123456789012345678"
    ch = _detail_ok(change_id=cid, sha=sha, cr_value=2, number=42)
    comments = {
        "f.py": [
            {
                "id": "TvcXrmjM",
                "line": 1,
                "message": "note",
                "unresolved": True,
                "author": {"username": "grt", "name": "Tobias Grün"},
            }
        ]
    }
    store = ChangeStore({str(ch["id"]): ch}, web_base="https://g.example")
    store.set_comments(str(ch["id"]), comments)
    code, out, _err = run_cli(
        web_stack_repo,
        gshow_main,
        ["--json", "change:42"],
        monkeypatch,
        gerrit=store,
    )
    assert code == 0
    data = json_stdout(out)
    assert data["comments"][0]["author"] == "grt (Tobias Grün)"


def test_gshow_help(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, _err = run_cli(stack_repo, gshow_main, ["--help"], monkeypatch, catch_sys_exit=True)
    assert code == 0
    assert "gshow" in out.lower() or "ger show" in out
    assert "REV" in out
    assert "--stack" in out
    assert "--comments" in out
    assert "--context" in out
    assert "--context-padding" in out
    assert "--hyperlinks" in out
    assert "--ai" in out or "markdown" in out


def test_gshow_human_head_formatting(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Text mode includes commit line, subject, and status prefix (no TTY colors)."""
    sha = git_out("rev-parse", "HEAD", cwd=web_stack_repo)
    subj = git_out("log", "-1", "--format=%s", cwd=web_stack_repo)
    cid = head_change_id(web_stack_repo)
    detail = change_info_for_sha(sha, cid, number=77)
    details = {str(detail["id"]): detail}
    code, out, err = run_cli(web_stack_repo, gshow_main, [], monkeypatch, gerrit=ChangeStore(details))
    assert code == 0, err
    assert "commit " in out
    assert git_out("rev-parse", "--short", sha, cwd=web_stack_repo) in out
    assert subj in out
    assert "Author:" in out
    assert "g.example/c/" in out or "/+/" in out


def test_gshow_hyperlinks_always_shows_open_in_gerrit(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sha = git_out("rev-parse", "HEAD", cwd=web_stack_repo)
    cid = head_change_id(web_stack_repo)
    detail = change_info_for_sha(sha, cid, number=77)
    details = {str(detail["id"]): detail}
    code, out, err = run_cli(
        web_stack_repo,
        gshow_main,
        ["--hyperlinks", "always", "--color=never"],
        monkeypatch,
        gerrit=ChangeStore(details),
    )
    assert code == 0, err
    assert "\x1b]8;;https://gerrit.example" in out
    visible = strip_ansi(out)
    assert GERRIT_LINK_LABEL in visible
    assert "url:" in visible
    assert "https://gerrit.example" not in visible


def test_gshow_markdown_ignores_hyperlinks(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sha = git_out("rev-parse", "HEAD", cwd=web_stack_repo)
    cid = head_change_id(web_stack_repo)
    detail = change_info_for_sha(sha, cid, number=77)
    details = {str(detail["id"]): detail}
    code, out, err = run_cli(
        web_stack_repo,
        gshow_main,
        ["--format", "markdown", "--hyperlinks", "always"],
        monkeypatch,
        gerrit=ChangeStore(details),
    )
    assert code == 0, err
    assert "\x1b]8;" not in out
    assert "- Gerrit: https://gerrit.example" in out
    assert GERRIT_LINK_LABEL not in out


def test_gshow_json_keeps_raw_url_with_hyperlinks(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sha = git_out("rev-parse", "HEAD", cwd=web_stack_repo)
    cid = head_change_id(web_stack_repo)
    detail = change_info_for_sha(sha, cid, number=77)
    details = {str(detail["id"]): detail}
    code, out, err = run_cli(
        web_stack_repo,
        gshow_main,
        ["--json", "--hyperlinks", "always"],
        monkeypatch,
        gerrit=ChangeStore(details),
    )
    assert code == 0, err
    data = json_stdout(out)
    assert "\x1b]8;" not in out
    assert (data.get("gerrit_url") or "").startswith("https://gerrit.example")


def test_gshow_unpushed_local_commit(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unpushed local commit: git message, not-pushed status line, no Gerrit comments."""
    cid = "Icccccccccccccccccccccccccccccccccccccccc"
    git(
        "commit",
        "--allow-empty",
        "-m",
        f"local only wip\n\nChange-Id: {cid}",
        cwd=web_stack_repo,
    )
    sha = git_out("rev-parse", "HEAD", cwd=web_stack_repo)
    code, out, err = run_cli(web_stack_repo, gshow_main, ["--color=never"], monkeypatch, gerrit=ChangeStore({}))
    assert code == 0, err
    assert "commit " in out
    assert git_out("rev-parse", "--short", sha, cwd=web_stack_repo) in out
    assert "local only wip" in out
    assert "v? " not in out
    assert "cr? " not in out
    assert "not-pushed" in out
    assert "╭" not in out


def test_gshow_json_unpushed_local_commit(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cid = "Idddddddddddddddddddddddddddddddddddddddd"
    git(
        "commit",
        "--allow-empty",
        "-m",
        f"not pushed yet\n\nChange-Id: {cid}",
        cwd=web_stack_repo,
    )
    code, out, err = run_cli(web_stack_repo, gshow_main, ["--json"], monkeypatch, gerrit=ChangeStore({}))
    assert code == 0, err
    data = json_stdout(out)
    assert data["pushed"] is False
    assert data["patchset_status"] == "absent"
    assert data["change_id"] == cid
    assert data["local_commit"] is True
    assert data["attention_reasons"] == ["not-pushed"]
    assert data["comments"] == []


def test_gshow_human_prints_no_unresolved_comments_when_clean(
    web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sha = git_out("rev-parse", "HEAD", cwd=web_stack_repo)
    cid = head_change_id(web_stack_repo)
    detail = change_info_for_sha(sha, cid, number=92)
    details = {str(detail["id"]): detail}
    code, out, err = run_cli(web_stack_repo, gshow_main, ["--color=never"], monkeypatch, gerrit=ChangeStore(details))
    assert code == 0, err
    assert "commit " in out
    assert "╭" not in out
    assert "Unresolved comments:" not in out


def test_gshow_highlights_warning_pattern_on_summary_line(
    web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sha = git_out("rev-parse", "HEAD", cwd=web_stack_repo)
    subj = git_out("log", "-1", "--format=%s", cwd=web_stack_repo)
    cid = head_change_id(web_stack_repo)
    git("config", "ger.stopPattern", r"^does-not-match$", cwd=web_stack_repo)
    git("config", "ger.warningPattern", subj, cwd=web_stack_repo)
    detail = change_info_for_sha(sha, cid, number=91)
    details = {str(detail["id"]): detail}
    code, out, err = run_cli(
        web_stack_repo, gshow_main, ["--color", "always"], monkeypatch, gerrit=ChangeStore(details)
    )
    assert code == 0, err
    assert ANSI_YELLOW in out
    assert subj in out


@pytest.mark.parametrize(
    "argv",
    [
        ["--json", "HEAD"],
        ["HEAD", "--verbose"],
    ],
    ids=["json", "verbose"],
)
def test_gshow_smoke_argv_head_mocked(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
    sha = git_out("rev-parse", "HEAD", cwd=web_stack_repo)
    cid = head_change_id(web_stack_repo)
    detail = change_info_for_sha(sha, cid, number=88)
    details = {str(detail["id"]): detail}
    code, _out, err = run_cli(web_stack_repo, gshow_main, argv, monkeypatch, gerrit=ChangeStore(details))
    assert code in (0, 1), err


def _mock_show_details(*rows: dict) -> dict[str, dict]:
    """Key by triplet; duplicate triplets get suffixed keys so all rows are kept."""
    out: dict[str, dict] = {}
    for row in rows:
        triplet = str(row["id"])
        key = triplet if triplet not in out else f"{triplet}#{row.get('_number', len(out))}"
        out[key] = row
    return out


def test_gshow_json_change_id_narrowing_includes_resolution(
    web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bare Change-Id on two branches narrows to target branch with resolution JSON."""
    cid = "Ibbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    sha = "abc12345678901234567890123456789012345678"
    main_row = _detail_ok(change_id=cid, sha=sha, number=120045, branch="main")
    dev_row = _detail_ok(change_id=cid, sha=sha, number=119870, branch="dev")
    details = _mock_show_details(main_row, dev_row)
    code, out, err = run_cli(web_stack_repo, gshow_main, ["--json", cid], monkeypatch, gerrit=ChangeStore(details))
    assert code == 0, err
    data = json_stdout(out)
    resolution = data["resolution"]
    assert resolution["kind"] == "change-id"
    assert resolution["ambiguous"] is True
    assert resolution["selected_reason"] == "target-branch"
    assert resolution["selected"]["number"] == 120045
    assert resolution["selected"]["branch"] == "main"
    assert len(resolution["alternatives"]) == 1
    assert resolution["alternatives"][0]["number"] == 119870


def test_gshow_stack_comments_use_triplet_when_change_id_on_two_branches(
    web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--stack`` must fetch comments by push-branch triplet, not a bare Change-Id.

    Gerrit 404s ``GET …/changes/<Change-Id>/comments`` when the same Change-Id exists on
    multiple branches ("Multiple changes found"). Stack expansion has no
    ``resolution.selected``, so the show path must build the triplet itself.
    """
    tip = git_out("rev-parse", "HEAD", cwd=web_stack_repo)
    tip_cid = git_out("log", "-1", "--format=%(trailers:key=Change-Id,valueonly)", tip, cwd=web_stack_repo).strip()
    main_row = _detail_ok(change_id=tip_cid, sha=tip, number=301, branch="main", cr_value=0)
    main_row["unresolved_comment_count"] = 1
    dev_row = _detail_ok(change_id=tip_cid, sha=tip, number=302, branch="dev", cr_value=0)
    store = ChangeStore(_mock_show_details(main_row, dev_row), web_base="https://g.example")
    store.set_comments(
        str(main_row["id"]),
        {
            "stack.py": [
                {
                    "id": "stack-c1",
                    "line": 1,
                    "message": "fix on push branch",
                    "unresolved": True,
                    "author": {"username": "alice", "name": "Alice"},
                }
            ]
        },
    )
    code, out, err = run_cli(
        web_stack_repo,
        gshow_main,
        ["--color=never", "--stack", "--comments", "unresolved"],
        monkeypatch,
        gerrit=store,
    )
    assert code in (0, 1), err
    assert "fix on push branch" in out
    comment_refs = [call.args[0] for call in store.calls_to("get_comments")]
    assert comment_refs, "expected at least one comments fetch"
    assert all("~" in ref for ref in comment_refs), comment_refs
    assert all(tip_cid not in ref or ref.endswith(f"~{tip_cid}") for ref in comment_refs)
    assert any(ref == f"testproj~main~{tip_cid}" for ref in comment_refs)


def test_gshow_ambiguity_after_narrowing_exits_four(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two open changes on the target branch exit with ambiguity code 4."""
    cid = "Icccccccccccccccccccccccccccccccccccccccc"
    sha = "def12345678901234567890123456789012345678"
    first = _detail_ok(change_id=cid, sha=sha, number=120045, branch="main")
    second = _detail_ok(change_id=cid, sha=sha, number=120046, branch="main")
    second["_number"] = 120046
    details = _mock_show_details(first, second)
    code, _out, err = run_cli(web_stack_repo, gshow_main, [cid], monkeypatch, gerrit=ChangeStore(details))
    assert code == ExitCode.AMBIGUOUS
    assert "ambiguous" in err.lower()


def test_gshow_detached_head_during_rebase(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """During rebase, ger show resolves stack context from the branch being rebased."""
    git("checkout", "--detach", "HEAD", cwd=web_stack_repo)
    write_rebase_head(web_stack_repo, "feature")
    sha = git_out("rev-parse", "HEAD", cwd=web_stack_repo)
    cid = head_change_id(web_stack_repo)
    detail = change_info_for_sha(sha, cid, number=77)
    details = {str(detail["id"]): detail}
    code, out, err = run_cli(web_stack_repo, gshow_main, ["HEAD"], monkeypatch, gerrit=ChangeStore(details))
    assert code == 0, err
    assert git_out("rev-parse", "--short", sha, cwd=web_stack_repo) in out


def test_gshow_stack_during_rebase(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("checkout", "--detach", "HEAD", cwd=web_stack_repo)
    write_rebase_head(web_stack_repo, "feature")
    code, out, err = run_cli(
        web_stack_repo,
        gshow_main,
        ["--json", "--stack"],
        monkeypatch,
        gerrit=ChangeStore({}),
    )
    assert code in (0, 1), err
    data = json_stdout(out)
    assert len(data["commits"]) >= 2


def test_gshow_picks_up_resolved_comments_when_updated_is_unchanged(
    web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same-second resolve: ``updated`` stays put, ``meta_rev_id`` moves, show must refresh."""

    force_zero_change_trust_window(monkeypatch)
    sha = git_out("rev-parse", "HEAD", cwd=web_stack_repo)
    cid = head_change_id(web_stack_repo)
    ch = _detail_ok(change_id=cid, sha=sha, cr_value=2, number=42)
    ch["updated"] = "2026-09-10 19:18:28.000000000"
    ch["meta_rev_id"] = "meta-open"
    ch["unresolved_comment_count"] = 1
    ch["total_comment_count"] = 1
    store = ChangeStore({str(ch["id"]): ch}, web_base="https://g.example")
    store.set_comments(
        str(ch["id"]),
        {
            "file.txt": [
                {"id": "c1", "line": 1, "message": "please fix", "unresolved": True},
            ]
        },
    )

    code, out, err = run_cli(web_stack_repo, gshow_main, ["--json", "HEAD"], monkeypatch, gerrit=store)
    assert code == 0, err
    data = json_stdout(out)
    assert any(c.get("message") == "please fix" for c in data.get("comments") or [])

    ch["meta_rev_id"] = "meta-resolved"
    ch["unresolved_comment_count"] = 0
    ch["total_comment_count"] = 2
    store.set_comments(
        str(ch["id"]),
        {
            "file.txt": [
                {"id": "c1", "line": 1, "message": "please fix", "unresolved": True},
                {"id": "c2", "line": 1, "message": "done", "unresolved": False, "in_reply_to": "c1"},
            ]
        },
    )

    code2, out2, err2 = run_cli(web_stack_repo, gshow_main, ["--json", "HEAD"], monkeypatch, gerrit=store)
    assert code2 == 0, err2
    data2 = json_stdout(out2)
    assert data2.get("comments") == []
    assert data2.get("comments_unresolved") == 0


def test_show_change_number_renders_the_local_commit(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = stack_rows_mb_to_head(web_stack_repo)
    store = ChangeStore(build_details_by_change_id(rows))

    code, out, err = run_cli(web_stack_repo, gshow_main, ["--json", "change:100"], monkeypatch, gerrit=store)

    assert code in (0, 1), err
    assert json.loads(out)["local_commit"] is True
    assert json.loads(out)["sha"] == rows[0].sha

    _code, out, _err = run_cli(web_stack_repo, gshow_main, ["--color=never", "change:100"], monkeypatch, gerrit=store)
    assert "Author:" in out


def test_show_text_has_subject_and_every_attention_reason_without_a_local_commit(
    web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    change = change_info_for_sha("a" * 40, "I" + "c" * 40, number=42, cr=0, reviewers=[])
    change["subject"] = "remote only subject"
    store = ChangeStore({str(change["id"]): change})

    _code, out, _err = run_cli(web_stack_repo, gshow_main, ["--color=never", "change:42"], monkeypatch, gerrit=store)

    assert "remote only subject" in out
    assert "attention: " in out
    assert "no-reviewers" in out
    assert "awaiting-review" in out


def _open_thread_with_draft_reply(store: ChangeStore, change_key: str) -> None:
    store.set_comments(
        change_key,
        {"f.py": [{"id": "root-id", "line": 1, "message": "please fix", "unresolved": True, "patch_set": 1}]},
    )
    store.set_drafts(
        change_key,
        {
            "f.py": [
                {
                    "id": "draft-id",
                    "line": 1,
                    "message": "Done",
                    "unresolved": False,
                    "in_reply_to": "root-id",
                    "patch_set": 1,
                }
            ]
        },
    )


def test_gshow_shows_your_draft_reply_in_its_chain(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ch = _detail_ok(change_id="I" + "b" * 40, sha="abc" + "1" * 37, cr_value=2, number=42)
    store = ChangeStore({str(ch["id"]): ch}, web_base="https://g.example")
    _open_thread_with_draft_reply(store, str(ch["id"]))

    code, out, _err = run_cli(web_stack_repo, gshow_main, ["--color=never", "change:42"], monkeypatch, gerrit=store)
    assert code == 0
    assert "please fix" in out
    assert "draft (you) -> resolves" in out
    assert out.index("please fix") < out.index("Done")

    code, out, _err = run_cli(web_stack_repo, gshow_main, ["--ai", "change:42"], monkeypatch, gerrit=store)
    assert "**Draft reply (unpublished) -> resolves**" in out


def test_gshow_json_tells_published_state_from_state_after_publish(
    web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ch = _detail_ok(change_id="I" + "b" * 40, sha="abc" + "1" * 37, cr_value=2, number=42)
    store = ChangeStore({str(ch["id"]): ch}, web_base="https://g.example")
    _open_thread_with_draft_reply(store, str(ch["id"]))

    code, out, _err = run_cli(web_stack_repo, gshow_main, ["--json", "change:42"], monkeypatch, gerrit=store)
    assert code == 0
    (chain,) = json_stdout(out)["comment_chains"]
    assert chain["resolved"] is False
    assert chain["resolved_after_publish"] is True
    assert [(c["comment_id"], c["draft"]) for c in chain["comments"]] == [("root-id", False), ("draft-id", True)]


def test_gshow_fetches_drafts_every_time(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Drafts never move the change's freshness token, so a cached copy could never be trusted."""
    ch = _detail_ok(change_id="I" + "b" * 40, sha="abc" + "1" * 37, cr_value=2, number=42)
    store = ChangeStore({str(ch["id"]): ch}, web_base="https://g.example")
    _open_thread_with_draft_reply(store, str(ch["id"]))
    run_cli(web_stack_repo, gshow_main, ["--json", "change:42"], monkeypatch, gerrit=store)
    store.set_drafts(str(ch["id"]), {})
    _code, out, _err = run_cli(web_stack_repo, gshow_main, ["--json", "change:42"], monkeypatch, gerrit=store)
    (chain,) = json_stdout(out)["comment_chains"]
    assert [c["draft"] for c in chain["comments"]] == [False]


def test_show_says_who_voted_what(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Votes per account, in text, --ai and --json: no REST detour to learn who blocks a change."""
    rows = stack_rows_mb_to_head(web_stack_repo)
    details = build_details_by_change_id(rows)
    head = details[list(details)[-1]]
    head["labels"] = {
        "Code-Review": {
            "all": [{"value": 2, "name": "Alice"}, {"value": -1, "username": "bob"}, {"value": 0, "name": "Carol"}]
        },
        "Verified": {"all": [{"value": 1, "name": "CI Bot"}]},
    }
    store = ChangeStore(details)
    code, out, err = run_cli(web_stack_repo, gshow_main, [], monkeypatch, gerrit=store)
    assert code == ExitCode.OK, err
    assert "votes: Code-Review +2 Alice, -1 bob · Verified +1 CI Bot" in out
    _code, out, _err = run_cli(web_stack_repo, gshow_main, ["--ai"], monkeypatch, gerrit=store)
    assert "- Votes: Code-Review +2 Alice, -1 bob · Verified +1 CI Bot" in out
    _code, out, _err = run_cli(web_stack_repo, gshow_main, ["--json"], monkeypatch, gerrit=store)
    data = json_stdout(out)
    votes = data.get("commits", [data])[-1]["votes"]
    assert {"label": "Code-Review", "value": -1, "by": "bob"} in votes and len(votes) == 3


def test_a_change_with_no_local_commit_shows_gerrits_whole_message_and_author(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding 9 of the 2026-11-25 review walk: outside the review worktree only the subject was shown."""
    from tests.test_waiting_on_your_review import _store, _theirs  # pylint: disable=import-outside-toplevel

    change = _theirs(900, asks_you=True)
    [revision] = change["revisions"].values()
    revision["commit"].update(
        message="Alice's change 900\n\nWhy it is needed, at length.\n\nChange-Id: " + change["change_id"] + "\n",
        author={"name": "Alice", "email": "alice@example.com", "date": "2026-11-08 09:00:00.000000000"},
    )
    store = _store(stack_repo, change)
    code, out, err = run_cli(stack_repo, gshow_main, ["change:900", "--color=never"], monkeypatch, gerrit=store)
    assert code == ExitCode.OK, err
    assert "Author: Alice <alice@example.com>" in out
    assert "    Why it is needed, at length." in out
    assert f"    Change-Id: {change['change_id']}" in out
