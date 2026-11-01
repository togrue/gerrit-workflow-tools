# Spec: docu/spec/commands/resolve.md

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_resolve import main as resolve_main
from gerrit_workflow_tools.core.git_run import git, git_out
from tests.change_store import ChangeStore
from tests.cli_gerrit_mocks import change_info_for_sha, head_change_id
from tests.conftest import json_stdout, run_cli
from tests.fixtures import _cid


CID_UNIQUE = _cid("01")
CID_NARROW = _cid("02")
CID_TRIPLET = _cid("03")
CID_NUMBER = _cid("04")
CID_REF = _cid("05")
CID_URL = _cid("06")
CID_QUERY = _cid("07")
CID_JSON = _cid("08")
CID_AMBIG = _cid("09")
CID_MISSING = _cid("0a")


def _configure_resolve_repo(stack_repo: Path) -> None:
    git("config", "gerrit.webUrl", "https://g.example", cwd=stack_repo)


def _detail(
    *,
    change_id: str,
    sha: str,
    number: int = 99,
    branch: str = "main",
    status: str = "NEW",
) -> dict[str, Any]:
    return change_info_for_sha(sha, change_id, number=number, branch=branch, status=status)


def _mock_details(*rows: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        triplet = str(row["id"])
        key = triplet if triplet not in out else f"{triplet}#{row.get('_number', len(out))}"
        out[key] = row
    return out


def test_resolve_git_rev_local_only(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Init commit on main has no Change-Id; resolves local SHA only."""
    _configure_resolve_repo(stack_repo)
    sha = git_out("rev-parse", "main", cwd=stack_repo)
    code, out, err = run_cli(stack_repo, resolve_main, ["main"], monkeypatch, gerrit=ChangeStore({}))
    assert code == 0, err
    assert f"local SHA: {sha}" in out
    assert "Gerrit change:" not in out


def test_resolve_git_rev_with_gerrit_footer(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_resolve_repo(stack_repo)
    sha = git_out("rev-parse", "HEAD", cwd=stack_repo)
    cid = head_change_id(stack_repo)
    ch = _detail(change_id=cid, sha=sha, number=104)
    details = {str(ch["id"]): ch}
    code, out, err = run_cli(stack_repo, resolve_main, ["HEAD"], monkeypatch, gerrit=ChangeStore(details))
    assert code == 0, err
    assert f"local SHA: {sha}" in out
    assert "Gerrit change: #104" in out
    assert str(ch["id"]) in out


def test_resolve_change_id_unique(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_resolve_repo(stack_repo)
    cid = CID_UNIQUE
    sha = git_out("rev-parse", "HEAD~1", cwd=stack_repo)
    ch = _detail(change_id=cid, sha=sha, number=120001)
    details = {str(ch["id"]): ch}
    code, out, err = run_cli(stack_repo, resolve_main, [cid], monkeypatch, gerrit=ChangeStore(details))
    assert code == 0, err
    assert "Gerrit change: #120001" in out
    assert cid in out
    assert "note:" not in err


def test_resolve_change_id_narrowed(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cid = CID_NARROW
    sha = git_out("rev-parse", "HEAD~1", cwd=stack_repo)
    main_row = _detail(change_id=cid, sha=sha, number=120045, branch="main")
    dev_row = _detail(change_id=cid, sha=sha, number=119870, branch="dev")
    details = _mock_details(main_row, dev_row)
    _configure_resolve_repo(stack_repo)
    code, out, err = run_cli(stack_repo, resolve_main, [cid], monkeypatch, gerrit=ChangeStore(details))
    assert code == 0, err
    assert "Gerrit change: #120045" in out
    assert "note:" in err
    assert "119870" in err


def test_resolve_triplet(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_resolve_repo(stack_repo)
    cid = CID_TRIPLET
    sha = git_out("rev-parse", "HEAD~1", cwd=stack_repo)
    ch = _detail(change_id=cid, sha=sha, number=120002)
    triplet = str(ch["id"])
    details = {triplet: ch}
    code, out, err = run_cli(stack_repo, resolve_main, [triplet], monkeypatch, gerrit=ChangeStore(details))
    assert code == 0, err
    assert f"Gerrit change: #120002 {triplet}" in out


def test_resolve_change_number(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_resolve_repo(stack_repo)
    cid = CID_NUMBER
    sha = git_out("rev-parse", "HEAD~1", cwd=stack_repo)
    ch = _detail(change_id=cid, sha=sha, number=120003)
    details = {str(ch["id"]): ch}
    code, out, err = run_cli(stack_repo, resolve_main, ["change:120003"], monkeypatch, gerrit=ChangeStore(details))
    assert code == 0, err
    assert "Gerrit change: #120003" in out


def test_resolve_change_ref(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_resolve_repo(stack_repo)
    cid = CID_REF
    sha = git_out("rev-parse", "HEAD~1", cwd=stack_repo)
    ch = _detail(change_id=cid, sha=sha, number=120004)
    details = {str(ch["id"]): ch}
    ref = "refs/changes/04/120004/1"
    code, out, err = run_cli(stack_repo, resolve_main, [ref], monkeypatch, gerrit=ChangeStore(details))
    assert code == 0, err
    assert "Gerrit change: #120004" in out


def test_resolve_url(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_resolve_repo(stack_repo)
    cid = CID_URL
    sha = git_out("rev-parse", "HEAD~1", cwd=stack_repo)
    ch = _detail(change_id=cid, sha=sha, number=120005)
    details = {str(ch["id"]): ch}
    url = "https://g.example/c/testproj/+/120005"
    code, out, err = run_cli(stack_repo, resolve_main, [url], monkeypatch, gerrit=ChangeStore(details))
    assert code == 0, err
    assert "Gerrit change: #120005" in out


@pytest.mark.parametrize(
    ("changeish", "message"),
    [
        ("http://evil.example/c/testproj/+/120005", "not on the configured Gerrit host"),
        ("https://g.example/c/otherproj/+/120005", "names project 'otherproj'"),
        ("refs/changes/99/120005/1", "lives under refs/changes/05/"),
        ("refs/changes/05/120005/99", "has no patch set 99"),
        ("change: 120005", "invalid change number"),
        ("change:0", "invalid change number"),
        ("change:-1", "invalid change number"),
    ],
)
def test_resolve_refuses_an_address_that_cannot_be_right(
    changeish: str, message: str, stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_resolve_repo(stack_repo)
    ch = _detail(change_id=CID_URL, sha=git_out("rev-parse", "HEAD~1", cwd=stack_repo), number=120005)
    code, _out, err = run_cli(
        stack_repo, resolve_main, [changeish], monkeypatch, gerrit=ChangeStore({str(ch["id"]): ch})
    )
    assert code == ExitCode.NOT_FOUND
    assert message in err


def test_resolve_reports_the_real_triplet_when_gerrit_sends_a_compact_id(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_resolve_repo(stack_repo)
    ch = _detail(change_id=CID_URL, sha=git_out("rev-parse", "HEAD~1", cwd=stack_repo), number=120005)
    triplet = str(ch["id"])
    ch["id"] = "testproj~120005"

    code, out, err = run_cli(
        stack_repo, resolve_main, ["change:120005", "--json"], monkeypatch, gerrit=ChangeStore({triplet: ch})
    )

    assert code == 0, err
    assert json_stdout(out)["resolution"]["selected"]["triplet"] == f"testproj~main~{CID_URL}"


def test_resolve_query(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_resolve_repo(stack_repo)
    cid = CID_QUERY
    sha = git_out("rev-parse", "HEAD~1", cwd=stack_repo)
    ch = _detail(change_id=cid, sha=sha, number=120006)
    details = {str(ch["id"]): ch}
    store = ChangeStore(details)
    store.stub_query("status:open", [ch])
    code, out, err = run_cli(stack_repo, resolve_main, ["q:status:open"], monkeypatch, gerrit=store)
    assert code == 0, err
    assert "Gerrit change: #120006" in out


def test_resolve_json_shape(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_resolve_repo(stack_repo)
    cid = CID_JSON
    sha = git_out("rev-parse", "HEAD~1", cwd=stack_repo)
    main_row = _detail(change_id=cid, sha=sha, number=120045, branch="main")
    dev_row = _detail(change_id=cid, sha=sha, number=119870, branch="dev")
    details = _mock_details(main_row, dev_row)
    code, out, err = run_cli(stack_repo, resolve_main, ["--json", cid], monkeypatch, gerrit=ChangeStore(details))
    assert code == 0, err
    data = json_stdout(out)
    assert set(data.keys()) == {"resolution"}
    resolution = data["resolution"]
    assert resolution["input"] == cid
    assert resolution["kind"] == "change-id"
    assert resolution["selected"]["number"] == 120045
    assert resolution["selected_reason"] == "target-branch"
    assert resolution["ambiguous"] is True
    assert len(resolution["alternatives"]) == 1
    assert "note:" in err


def test_resolve_ambiguous_exits_four(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_resolve_repo(stack_repo)
    cid = CID_AMBIG
    sha = git_out("rev-parse", "HEAD~1", cwd=stack_repo)
    first = _detail(change_id=cid, sha=sha, number=120045, branch="main")
    second = _detail(change_id=cid, sha=sha, number=120046, branch="main")
    details = _mock_details(first, second)
    code, _out, err = run_cli(stack_repo, resolve_main, [cid], monkeypatch, gerrit=ChangeStore(details))
    assert code == ExitCode.AMBIGUOUS
    assert "ambiguous" in err.lower()


def test_resolve_not_found_exits_three(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_resolve_repo(stack_repo)
    cid = CID_MISSING
    code, _out, err = run_cli(stack_repo, resolve_main, [cid], monkeypatch, gerrit=ChangeStore({}))
    assert code == ExitCode.NOT_FOUND
    assert "error:" in err.lower()


def test_resolve_usage_missing_changeish(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, _out, err = run_cli(stack_repo, resolve_main, [], monkeypatch, catch_sys_exit=True)
    assert code == 2
    assert "CHANGEISH" in err or "changeish" in err.lower()


def test_ger_dispatches_resolve(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from gerrit_workflow_tools.cli_ger import main as ger_main

    code, out, _err = run_cli(stack_repo, ger_main, ["resolve", "--help"], monkeypatch, catch_sys_exit=True)
    assert code == 0
    assert "ger resolve" in out
