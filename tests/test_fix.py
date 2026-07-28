from __future__ import annotations

import sys
from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_fix import main as ger_fix_main
from gerrit_workflow_tools.core.change_id import extract_valid_change_id
from gerrit_workflow_tools.core.git_run import git, git_out
from tests.change_store import ChangeStore
from tests.cli_gerrit_mocks import change_info_for_sha
from tests.conftest import run_cli
from tests.fixtures import _cid


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
    sha = git_out("rev-parse", "HEAD~1", cwd=stack_repo)
    # The change must carry the footer that is actually on that commit: a ChangeInfo whose
    # change_id disagrees with the commit it points at cannot occur in a real repository.
    cid = extract_valid_change_id(git_out("log", "-1", "--format=%B", sha, cwd=stack_repo))
    assert cid
    ch = change_info_for_sha(sha, cid, number=4242)
    ch["revisions"][sha]["ref"] = "refs/changes/42/4242/1"
    details = {str(ch["id"]): ch}
    (stack_repo / "b.txt").write_text("via gerrit\n", encoding="utf-8")
    git("add", "b.txt", cwd=stack_repo)
    code, _out, err = run_cli(stack_repo, ger_fix_main, ["change:4242"], monkeypatch, gerrit=ChangeStore(details))
    assert code == 0, err
    subj = git_out("log", "-1", "--format=%s", cwd=stack_repo)
    assert subj.startswith("fixup! ")


def test_ger_fix_change_id_needs_no_gerrit_config(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A Change-Id is matched against the local stack, so gerrit.webUrl is irrelevant (ADR-0003).

    It used to be a CONFIG error: resolution went through Gerrit even though the answer was
    always a local commit."""
    cid = "Ibbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    code, _out, err = run_cli(stack_repo, ger_fix_main, [cid], monkeypatch)
    assert code == ExitCode.NOT_FOUND
    assert "no commit in current stack" in err
    assert "gerrit.webUrl" not in err


def test_ger_fix_change_not_on_stack_is_an_error(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Gerrit pointing at an object we do not have is a plain error now — no fetch (ADR-0003)."""
    git("config", "gerrit.webUrl", "https://g.example", cwd=stack_repo)
    missing = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    cid = "Icccccccccccccccccccccccccccccccccccccccc"
    ch = change_info_for_sha(missing, cid, number=7777)
    ch["revisions"][missing]["ref"] = "refs/changes/77/7777/3"
    details = {str(ch["id"]): ch}
    (stack_repo / "c.txt").write_text("no fetch path\n", encoding="utf-8")
    git("add", "c.txt", cwd=stack_repo)
    code, _out, err = run_cli(stack_repo, ger_fix_main, ["change:7777"], monkeypatch, gerrit=ChangeStore(details))
    assert code == ExitCode.NOT_FOUND
    assert "no commit in current stack" in err
    assert "fetch" not in err.lower()


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

    # Guards the shared resolver now, since that is where the client gets built.
    monkeypatch.setattr("gerrit_workflow_tools.core.gerrit.change_resolution.HttpGerritRest", _gerrit_must_not_run)
    monkeypatch.setattr(
        "gerrit_workflow_tools.core.gerrit.change_resolution.resolve_gerrit_web_base", _gerrit_must_not_run
    )

    (stack_repo / "a.txt").write_text("bare int\n", encoding="utf-8")
    git("add", "a.txt", cwd=stack_repo)
    code, _out, err = run_cli(stack_repo, ger_fix_main, ["42424242"], monkeypatch)
    assert code != 0
    assert "not a valid commit" in err.lower()


def test_ger_fix_json_includes_resolution_for_change_id(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("config", "gerrit.webUrl", "https://g.example", cwd=stack_repo)
    sha = git_out("rev-parse", "HEAD~1", cwd=stack_repo)
    cid = extract_valid_change_id(git_out("log", "-1", "--format=%B", sha, cwd=stack_repo))
    assert cid
    ch = change_info_for_sha(sha, cid, number=5151)
    details = {str(ch["id"]): ch}
    (stack_repo / "d.txt").write_text("json fix\n", encoding="utf-8")
    git("add", "d.txt", cwd=stack_repo)
    code, out, err = run_cli(stack_repo, ger_fix_main, ["--json", cid], monkeypatch, gerrit=ChangeStore(details))
    assert code == 0, err
    import json

    data = json.loads(out)
    assert data["fixup_sha"]
    # No `selected`: a Change-Id resolves against the local stack without asking Gerrit, so
    # there is no narrowing to report (ADR-0003).
    assert "resolution" not in data


def test_ger_fix_ambiguous_change_id_exits_4(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ambiguity is now a property of the stack, not of Gerrit (ADR-0003).

    `ger fix` never asks Gerrit which change a Change-Id names, so a Change-Id on several
    Gerrit branches is no longer ambiguous here. Two *stack commits* sharing one still are.
    """
    cid = _cid("2")
    (stack_repo / "dup.txt").write_text("duplicate change-id\n", encoding="utf-8")
    git("add", "dup.txt", cwd=stack_repo)
    git("commit", "-m", f"Duplicate footer\n\nChange-Id: {cid}", cwd=stack_repo)

    (stack_repo / "e.txt").write_text("ambig\n", encoding="utf-8")
    git("add", "e.txt", cwd=stack_repo)
    code, _out, err = run_cli(stack_repo, ger_fix_main, [cid], monkeypatch)
    assert code == ExitCode.AMBIGUOUS
    assert "ambiguous" in err.lower()
