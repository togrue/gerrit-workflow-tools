from __future__ import annotations

import sys
from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_fix import main as ger_fix_main
from gerrit_workflow_tools.core.config import clear_gerrit_git_config_cache
from gerrit_workflow_tools.core.git_run import git, git_out
from tests.change_store import ChangeStore
from tests.cli_gerrit_mocks import change_info_for_sha
from tests.conftest import run_cli


def test_ger_fix_requires_staged_changes(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (stack_repo / "a.txt").write_text("unstaged only\n", encoding="utf-8")
    code, _out, err = run_cli(stack_repo, ger_fix_main, ["HEAD~1"], monkeypatch)
    assert code == 1
    assert "staged" in err.lower()


def test_ger_fix_prompt_stages_on_yes(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (stack_repo / "a.txt").write_text("prompt yes\n", encoding="utf-8")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "y")
    code, _out, err = run_cli(stack_repo, ger_fix_main, ["HEAD~1"], monkeypatch)
    assert code == 0, err
    subj = git_out("log", "-1", "--format=%s", cwd=stack_repo)
    assert subj.startswith("fixup! ")
    assert "No staged changes" in err


def test_ger_fix_prompt_declines_on_no(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (stack_repo / "a.txt").write_text("prompt no\n", encoding="utf-8")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "n")
    code, _out, err = run_cli(stack_repo, ger_fix_main, ["HEAD~1"], monkeypatch)
    assert code == 1
    assert "staged" in err.lower()
    # Declined: working tree still dirty, index still empty
    assert git("diff", "--quiet", cwd=stack_repo, check=False).returncode != 0
    assert git("diff", "--cached", "--quiet", cwd=stack_repo, check=False).returncode == 0


def test_ger_fix_prompt_diff_then_yes(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (stack_repo / "a.txt").write_text("see the diff\n", encoding="utf-8")
    answers = iter(["d", "y"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    code, _out, err = run_cli(stack_repo, ger_fix_main, ["HEAD~1"], monkeypatch)
    assert code == 0, err
    assert "see the diff" in err
    assert "diff --git" in err or "---" in err
    subj = git_out("log", "-1", "--format=%s", cwd=stack_repo)
    assert subj.startswith("fixup! ")


def test_ger_fix_commit_fixup_on_ref(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = git_out("rev-parse", "HEAD~2", cwd=stack_repo)
    (stack_repo / "a.txt").write_text("patched\n", encoding="utf-8")
    git("add", "a.txt", cwd=stack_repo)
    code, _out, err = run_cli(stack_repo, ger_fix_main, [target], monkeypatch)
    assert code == 0, err
    subj = git_out("log", "-1", "--format=%s", cwd=stack_repo)
    assert subj.startswith("fixup! ")


def test_ger_fix_refs_changes_local_ref(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``refs/changes/…`` form resolves via local ref (no fetch)."""
    tip = git_out("rev-parse", "HEAD~1", cwd=stack_repo)
    git("update-ref", "refs/changes/07/12345/2", tip, cwd=stack_repo)
    (stack_repo / "d.txt").write_text("touch d\n", encoding="utf-8")
    git("add", "d.txt", cwd=stack_repo)
    code, _out, err = run_cli(stack_repo, ger_fix_main, ["refs/changes/07/12345/2"], monkeypatch)
    assert code == 0, err
    subj = git_out("log", "-1", "--format=%s", cwd=stack_repo)
    assert subj.startswith("fixup! ")


def test_ger_fix_all_flag(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (stack_repo / "a.txt").write_text("all mode\n", encoding="utf-8")
    code, _out, err = run_cli(stack_repo, ger_fix_main, ["-a", "HEAD~1"], monkeypatch)
    assert code == 0, err
    subj = git_out("log", "-1", "--format=%s", cwd=stack_repo)
    assert subj.startswith("fixup! ")


def test_ger_fix_numeric_change_uses_gerrit_revision(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "gerrit.webUrl", "https://g.example", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    sha = git_out("rev-parse", "HEAD~1", cwd=stack_repo)
    cid = "Ibbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    ch = change_info_for_sha(sha, cid, number=4242)
    ch["revisions"][sha]["ref"] = "refs/changes/42/4242/1"
    details = {str(ch["id"]): ch}
    (stack_repo / "b.txt").write_text("via gerrit\n", encoding="utf-8")
    git("add", "b.txt", cwd=stack_repo)
    code, _out, err = run_cli(stack_repo, ger_fix_main, ["change:4242"], monkeypatch, gerrit=ChangeStore(details))
    assert code == 0, err
    subj = git_out("log", "-1", "--format=%s", cwd=stack_repo)
    assert subj.startswith("fixup! ")


def test_ger_fix_missing_weburl_for_change_id(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clear_gerrit_git_config_cache()
    cid = "Ibbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    code, _out, err = run_cli(stack_repo, ger_fix_main, [cid], monkeypatch)
    assert code == 1
    assert "gerrit.webUrl" in err


def test_ger_fix_gerrit_missing_local_object_reports_fetch_error(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When Gerrit points at an object not in the repo, we try ``git fetch`` and surface failure."""
    git("config", "gerrit.webUrl", "https://g.example", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    missing = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    cid = "Icccccccccccccccccccccccccccccccccccccccc"
    ch = change_info_for_sha(missing, cid, number=7777)
    ch["revisions"][missing]["ref"] = "refs/changes/77/7777/3"
    details = {str(ch["id"]): ch}
    (stack_repo / "c.txt").write_text("fetch path\n", encoding="utf-8")
    git("add", "c.txt", cwd=stack_repo)
    code, _out, err = run_cli(stack_repo, ger_fix_main, ["change:7777"], monkeypatch, gerrit=ChangeStore(details))
    assert code != 0
    combined = f"{err} {_out}".lower()
    assert "refs/changes/77/7777/3" in err
    assert "fetch" in combined


def test_cli_ger_dispatches_fix(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from gerrit_workflow_tools.cli_ger import main as ger_main

    (stack_repo / "a.txt").write_text("via ger\n", encoding="utf-8")
    git("add", "a.txt", cwd=stack_repo)
    code, _out, err = run_cli(stack_repo, ger_main, ["fix", "HEAD~1"], monkeypatch)
    assert code == 0, err
    subj = git_out("log", "-1", "--format=%s", cwd=stack_repo)
    assert subj.startswith("fixup! ")


def test_ger_fix_bare_integer_is_git_revision_not_change_number(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bare digits resolve as a git revision, not a Gerrit change number (spec §2.2)."""

    def _gerrit_must_not_run(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Gerrit must not be consulted for a bare integer changeish")

    monkeypatch.setattr("gerrit_workflow_tools.cli_fix.HttpGerritRest", _gerrit_must_not_run)
    monkeypatch.setattr("gerrit_workflow_tools.cli_fix.resolve_gerrit_web_base", _gerrit_must_not_run)

    (stack_repo / "a.txt").write_text("bare int\n", encoding="utf-8")
    git("add", "a.txt", cwd=stack_repo)
    code, _out, err = run_cli(stack_repo, ger_fix_main, ["42424242"], monkeypatch)
    assert code != 0
    assert "not a valid commit" in err.lower()


def test_ger_fix_json_includes_resolution_for_change_id(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "gerrit.webUrl", "https://g.example", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    sha = git_out("rev-parse", "HEAD~1", cwd=stack_repo)
    cid = "Idddddddddddddddddddddddddddddddddddddddd"
    ch = change_info_for_sha(sha, cid, number=5151)
    details = {str(ch["id"]): ch}
    (stack_repo / "d.txt").write_text("json fix\n", encoding="utf-8")
    git("add", "d.txt", cwd=stack_repo)
    code, out, err = run_cli(stack_repo, ger_fix_main, ["--json", cid], monkeypatch, gerrit=ChangeStore(details))
    assert code == 0, err
    import json

    data = json.loads(out)
    assert data["fixup_sha"]
    assert data["resolution"]["kind"] == "change-id"
    assert data["resolution"]["selected"]["change_id"] == cid


def test_ger_fix_ambiguous_change_id_exits_4(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from gerrit_workflow_tools.cli_common import EXIT_AMBIGUOUS

    git("config", "gerrit.webUrl", "https://g.example", cwd=stack_repo)
    clear_gerrit_git_config_cache()
    cid = "Ieeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
    sha = git_out("rev-parse", "HEAD~1", cwd=stack_repo)
    first = change_info_for_sha(sha, cid, number=120045, branch="main")
    second = change_info_for_sha(sha, cid, number=120046, branch="main")
    triplet = str(first["id"])
    details = {triplet: first, f"{triplet}#120046": second}
    (stack_repo / "e.txt").write_text("ambig\n", encoding="utf-8")
    git("add", "e.txt", cwd=stack_repo)
    code, _out, err = run_cli(stack_repo, ger_fix_main, [cid], monkeypatch, gerrit=ChangeStore(details))
    assert code == EXIT_AMBIGUOUS
    assert "ambiguous" in err.lower()
