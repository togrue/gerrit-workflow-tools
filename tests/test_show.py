# Spec: docu/spec/commands/show.md
# Covers: HEAD query, --json, --verbose, warning highlighting, invalid change number

from __future__ import annotations

from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_show import main as gshow_main
from gerrit_workflow_tools.cli_style import ANSI_YELLOW
from gerrit_workflow_tools.core.gerrit.rest import LOG_QUERY_OPTIONS
from gerrit_workflow_tools.core.git_run import git, git_out
from tests.change_store import ChangeStore
from tests.cli_gerrit_mocks import (
    change_info_for_sha,
    head_change_id,
)
from tests.conftest import json_stdout, run_cli


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


def test_gshow_accepts_git_range(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ranges expand to local commits; multi-commit ranges wrap JSON in commits[]."""
    git("config", "gerrit.webUrl", "https://g.example", cwd=stack_repo)
    # Stack is upstream..HEAD with multiple commits; use main..HEAD for 2+.
    tip = git_out("rev-parse", "HEAD", cwd=stack_repo)
    store = ChangeStore({})
    code, out, err = run_cli(
        stack_repo,
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


def test_gshow_stack_json(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "gerrit.webUrl", "https://g.example", cwd=stack_repo)
    code, out, err = run_cli(
        stack_repo,
        gshow_main,
        ["--json", "--stack"],
        monkeypatch,
        gerrit=ChangeStore({}),
    )
    assert code in (0, 1), err
    data = json_stdout(out)
    assert "commits" in data
    assert len(data["commits"]) >= 2


def test_gshow_markdown_ai_format(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "gerrit.webUrl", "https://g.example", cwd=stack_repo)
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
        stack_repo,
        gshow_main,
        ["--ai", "change:42"],
        monkeypatch,
        gerrit=store,
    )
    assert code == 1
    assert "## " in out
    assert "### `epsilon.txt:908`" in out
    assert "**alice (Alice)**" in out or "**Alice**" in out or "alice" in out.lower()
    assert "> please fix" in out
    assert "> looking" in out
    assert "\033[" not in out


def test_gshow_human_thread_gutter(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "gerrit.webUrl", "https://g.example", cwd=stack_repo)
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
        stack_repo,
        gshow_main,
        ["--color=never", "change:42"],
        monkeypatch,
        gerrit=store,
    )
    assert code == 1
    assert "f.py:1" in out
    assert "└ " in out
    assert "looking" in out


def test_gshow_multi_arg_json_wraps(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "gerrit.webUrl", "https://g.example", cwd=stack_repo)
    tip = git_out("rev-parse", "HEAD", cwd=stack_repo)
    parent = git_out("rev-parse", "HEAD~1", cwd=stack_repo)
    code, out, err = run_cli(
        stack_repo,
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
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: bare ``changes/?q=`` omits ``current_revision`` unless ``o=CURRENT_REVISION``."""
    git("config", "gerrit.webUrl", "https://g.example", cwd=stack_repo)
    cid = "Ibbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    sha = "abc12345678901234567890123456789012345678"
    ch = _detail_ok(change_id=cid, sha=sha, cr_value=2, number=42)
    store = ChangeStore({str(ch["id"]): ch}, web_base="https://g.example")
    code, out, _err = run_cli(
        stack_repo,
        gshow_main,
        ["--json", cid],
        monkeypatch,
        gerrit=store,
    )
    assert code == 0
    data = json_stdout(out)
    assert data["sha"] == sha
    assert any(call.kwargs.get("options") == list(LOG_QUERY_OPTIONS) for call in store.calls_to("query_changes"))


def test_gshow_json_numeric_change_mocked(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "gerrit.webUrl", "https://g.example", cwd=stack_repo)
    cid = "Ibbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    sha = "abc12345678901234567890123456789012345678"
    ch = _detail_ok(change_id=cid, sha=sha, cr_value=2, number=42)
    ch["_number"] = 42
    store = ChangeStore({str(ch["id"]): ch}, web_base="https://g.example")
    code, out, _err = run_cli(
        stack_repo,
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


def test_gshow_json_attention_mocked(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "gerrit.webUrl", "https://g.example", cwd=stack_repo)
    cid = "Ibbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    sha = "abc12345678901234567890123456789012345678"
    ch = _detail_ok(change_id=cid, sha=sha, cr_value=1, number=42)
    store = ChangeStore({str(ch["id"]): ch}, web_base="https://g.example")
    code, out, _err = run_cli(
        stack_repo,
        gshow_main,
        ["--json", "change:42"],
        monkeypatch,
        gerrit=store,
    )
    assert code == 1
    data = json_stdout(out)
    assert "awaiting-review" in data["attention_reasons"]


def test_gshow_json_full_comment_ignores_comment_tail_lines(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--comment-tail-lines only affects text mode; JSON always has the full message."""
    git("config", "gerrit.webUrl", "https://g.example", cwd=stack_repo)
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
            }
        ]
    }
    store = ChangeStore({str(ch["id"]): ch}, web_base="https://g.example")
    store.set_comments(str(ch["id"]), comments)
    code, out, _err = run_cli(
        stack_repo,
        gshow_main,
        ["--json", "change:42", "--comment-tail-lines", "3"],
        monkeypatch,
        gerrit=store,
    )
    assert code == 0
    data = json_stdout(out)
    c0 = data["comments"][0]
    assert c0["message"] == long_msg
    assert "line0" in c0["message"] and "line14" in c0["message"]
    assert "lines omitted above" not in c0["message"]
    assert data["comments"][0]["url"] == "https://g.example/c/testproj/+/42/comment/TvcXrmjM/"


def test_gshow_skips_resolved_comment_chain(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolved thread: last reply unresolved=false hides entire chain."""
    git("config", "gerrit.webUrl", "https://g.example", cwd=stack_repo)
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
        stack_repo,
        gshow_main,
        ["--color=never", "change:42"],
        monkeypatch,
        gerrit=store,
    )
    assert code == 0
    assert "please fix" not in out
    assert "done" not in out
    assert "  (no unresolved comments)" in out


def test_gshow_human_shows_comment_author(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "gerrit.webUrl", "https://g.example", cwd=stack_repo)
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
        stack_repo,
        gshow_main,
        ["--color=never", "change:42"],
        monkeypatch,
        gerrit=store,
    )
    assert code == 1
    assert "epsilon.txt:908" in out
    assert "grt (Tobias Grün)" in out
    assert "    some comment" in out


def test_gshow_json_includes_comment_author(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "gerrit.webUrl", "https://g.example", cwd=stack_repo)
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
        stack_repo,
        gshow_main,
        ["--json", "change:42"],
        monkeypatch,
        gerrit=store,
    )
    assert code == 0
    data = json_stdout(out)
    assert data["comments"][0]["author"] == "grt (Tobias Grün)"


def test_gshow_full_comment_json(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "gerrit.webUrl", "https://g.example", cwd=stack_repo)
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
            }
        ]
    }
    store = ChangeStore({str(ch["id"]): ch}, web_base="https://g.example")
    store.set_comments(str(ch["id"]), comments)
    code, out, _err = run_cli(
        stack_repo,
        gshow_main,
        ["--json", "change:42", "--full"],
        monkeypatch,
        gerrit=store,
    )
    assert code == 0
    data = json_stdout(out)
    c0 = data["comments"][0]
    assert c0["message"] == long_msg
    assert "line0" in c0["message"] and "line14" in c0["message"]
    assert data["comments"][0]["url"] == "https://g.example/c/testproj/+/42/comment/TvcXrmjM/"


def _configure_gshow_repo(stack_repo: Path) -> None:
    git("config", "gerrit.webUrl", "https://g.example", cwd=stack_repo)


def test_gshow_help(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, _err = run_cli(stack_repo, gshow_main, ["--help"], monkeypatch, catch_sys_exit=True)
    assert code == 0
    assert "gshow" in out.lower() or "ger show" in out
    assert "REV" in out
    assert "--stack" in out
    assert "--ai" in out or "markdown" in out


def test_gshow_human_head_formatting(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Text mode includes commit line, subject, and status prefix (no TTY colors)."""
    _configure_gshow_repo(stack_repo)
    sha = git_out("rev-parse", "HEAD", cwd=stack_repo)
    subj = git_out("log", "-1", "--format=%s", cwd=stack_repo)
    cid = head_change_id(stack_repo)
    detail = change_info_for_sha(sha, cid, number=77)
    details = {str(detail["id"]): detail}
    code, out, err = run_cli(stack_repo, gshow_main, [], monkeypatch, gerrit=ChangeStore(details))
    assert code == 0, err
    assert "commit " in out and sha in out
    assert sha[:8] in out
    assert subj in out
    assert "g.example/c/" in out or "/+/" in out


def test_gshow_unpushed_local_commit(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unpushed local commit: git message, not-pushed status line, no Gerrit comments."""
    _configure_gshow_repo(stack_repo)
    cid = "Icccccccccccccccccccccccccccccccccccccccc"
    git(
        "commit",
        "--allow-empty",
        "-m",
        f"local only wip\n\nChange-Id: {cid}",
        cwd=stack_repo,
    )
    sha = git_out("rev-parse", "HEAD", cwd=stack_repo)
    code, out, err = run_cli(stack_repo, gshow_main, ["--color=never"], monkeypatch, gerrit=ChangeStore({}))
    assert code == 1, err
    assert "commit " in out and sha in out
    assert "local only wip" in out
    assert "v? " not in out
    assert "cr? " not in out
    assert "not-pushed" in out
    assert "(not on Gerrit — no comments)" in out
    assert "(no unresolved comments)" not in out


def test_gshow_json_unpushed_local_commit(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_gshow_repo(stack_repo)
    cid = "Idddddddddddddddddddddddddddddddddddddddd"
    git(
        "commit",
        "--allow-empty",
        "-m",
        f"not pushed yet\n\nChange-Id: {cid}",
        cwd=stack_repo,
    )
    code, out, err = run_cli(stack_repo, gshow_main, ["--json"], monkeypatch, gerrit=ChangeStore({}))
    assert code == 1, err
    data = json_stdout(out)
    assert data["pushed"] is False
    assert data["patchset_status"] == "absent"
    assert data["change_id"] == cid
    assert data["local_commit"] is True
    assert data["attention_reasons"] == ["not-pushed"]
    assert data["comments"] == []


def test_gshow_human_prints_no_unresolved_comments_when_clean(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_gshow_repo(stack_repo)
    sha = git_out("rev-parse", "HEAD", cwd=stack_repo)
    cid = head_change_id(stack_repo)
    detail = change_info_for_sha(sha, cid, number=92)
    details = {str(detail["id"]): detail}
    code, out, err = run_cli(stack_repo, gshow_main, ["--color=never"], monkeypatch, gerrit=ChangeStore(details))
    assert code == 0, err
    assert "Unresolved comments:" in out
    assert "  (no unresolved comments)" in out


def test_gshow_highlights_warning_pattern_on_summary_line(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_gshow_repo(stack_repo)
    sha = git_out("rev-parse", "HEAD", cwd=stack_repo)
    subj = git_out("log", "-1", "--format=%s", cwd=stack_repo)
    cid = head_change_id(stack_repo)
    git("config", "gerrit.stopPattern", r"^does-not-match$", cwd=stack_repo)
    git("config", "gerrit.warningPattern", subj, cwd=stack_repo)
    detail = change_info_for_sha(sha, cid, number=91)
    details = {str(detail["id"]): detail}
    code, out, err = run_cli(stack_repo, gshow_main, ["--color", "always"], monkeypatch, gerrit=ChangeStore(details))
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
def test_gshow_smoke_argv_head_mocked(stack_repo: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
    _configure_gshow_repo(stack_repo)
    sha = git_out("rev-parse", "HEAD", cwd=stack_repo)
    cid = head_change_id(stack_repo)
    detail = change_info_for_sha(sha, cid, number=88)
    details = {str(detail["id"]): detail}
    code, _out, err = run_cli(stack_repo, gshow_main, argv, monkeypatch, gerrit=ChangeStore(details))
    assert code in (0, 1), err


def _mock_show_details(*rows: dict) -> dict[str, dict]:
    """Key by triplet; duplicate triplets get suffixed keys so all rows are kept."""
    out: dict[str, dict] = {}
    for row in rows:
        triplet = str(row["id"])
        key = triplet if triplet not in out else f"{triplet}#{row.get('_number', len(out))}"
        out[key] = row
    return out


def test_gshow_json_change_id_narrowing_includes_resolution(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Bare Change-Id on two branches narrows to target branch with resolution JSON."""
    _configure_gshow_repo(stack_repo)
    cid = "Ibbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    sha = "abc12345678901234567890123456789012345678"
    main_row = _detail_ok(change_id=cid, sha=sha, number=120045, branch="main")
    dev_row = _detail_ok(change_id=cid, sha=sha, number=119870, branch="dev")
    details = _mock_show_details(main_row, dev_row)
    code, out, err = run_cli(stack_repo, gshow_main, ["--json", cid], monkeypatch, gerrit=ChangeStore(details))
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


def test_gshow_ambiguity_after_narrowing_exits_four(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two open changes on the target branch exit with ambiguity code 4."""
    _configure_gshow_repo(stack_repo)
    cid = "Icccccccccccccccccccccccccccccccccccccccc"
    sha = "def12345678901234567890123456789012345678"
    first = _detail_ok(change_id=cid, sha=sha, number=120045, branch="main")
    second = _detail_ok(change_id=cid, sha=sha, number=120046, branch="main")
    second["_number"] = 120046
    details = _mock_show_details(first, second)
    code, _out, err = run_cli(stack_repo, gshow_main, [cid], monkeypatch, gerrit=ChangeStore(details))
    assert code == ExitCode.AMBIGUOUS
    assert "ambiguous" in err.lower()
