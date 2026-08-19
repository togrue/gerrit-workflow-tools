# Spec: docu/spec/commands/inbox.md — to-review overview

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_inbox import DEFAULT_TO_REVIEW_QUERY, build_to_review_query
from gerrit_workflow_tools.cli_inbox import main as inbox_main
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.service import GerritService
from tests.change_store import ChangeStore
from tests.conftest import json_stdout, run_cli

SELF = 1000
TO_REVIEW_QUERY = f"{DEFAULT_TO_REVIEW_QUERY} label:Verified+1"


def _change(
    *,
    number: int,
    sha: str,
    parent: str | None,
    subject: str,
    owner: str = "alice",
    updated: str,
    attention_since: str | None = None,
    verified: int = 1,
    comments: int = 0,
    cr: int = 0,
) -> dict[str, Any]:
    change_id = "I" + f"{number:040x}"[-40:]
    payload: dict[str, Any] = {
        "id": f"myproject~main~{change_id}",
        "change_id": change_id,
        "project": "myproject",
        "branch": "main",
        "_number": number,
        "status": "NEW",
        "subject": subject,
        "owner": {"name": owner, "email": f"{owner}@example.com", "_account_id": 2},
        "current_revision": sha,
        "updated": updated,
        "created": updated,
        "unresolved_comment_count": comments,
        "revisions": {
            sha: {
                "_number": 1,
                "created": updated,
                "commit": {"parents": [{"commit": parent}] if parent else [], "subject": subject},
            }
        },
        "labels": {
            "Verified": {"value": verified, "all": [{"value": verified}]},
            "Code-Review": {"value": cr, "all": [{"value": cr}]},
        },
    }
    if attention_since is not None:
        payload["attention_set"] = {
            str(SELF): {
                "account": {"_account_id": SELF, "name": "me"},
                "last_update": attention_since,
            }
        }
    return payload


def _store(*rows: dict[str, Any], stub: list[dict[str, Any]] | None = None) -> ChangeStore:
    store = ChangeStore(
        {str(row["id"]): row for row in rows},
        web_base="https://gerrit.example.com",
        accounts={SELF: {"_account_id": SELF, "username": "me", "name": "me"}},
        self_account_id=SELF,
    )
    store.stub_query(TO_REVIEW_QUERY, stub if stub is not None else list(rows))
    return store


def test_inbox_help(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, _err = run_cli(tmp_path, inbox_main, ["--help"], monkeypatch, catch_sys_exit=True)
    assert code == 0
    assert "ger inbox" in out
    assert "--json" in out
    assert "--to-review" in out


def test_empty_inbox_is_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store()
    store.stub_query(TO_REVIEW_QUERY, [])
    code, out, err = run_cli(tmp_path, inbox_main, ["--color=never"], monkeypatch, gerrit=store)
    assert code == int(ExitCode.OK), err
    assert "to review (0)" in out
    assert "(nothing to review)" in out


def test_to_review_shows_unreviewed_age_activity_and_top_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _change(
        number=4317,
        sha="aaa",
        parent="origin",
        subject="feat: config plumbing",
        updated="2026-08-17 12:00:00.000000000",
        attention_since="2026-08-15 12:00:00.000000000",
    )
    top = _change(
        number=4321,
        sha="bbb",
        parent="aaa",
        subject="feat: rate limiter",
        updated="2026-08-18 08:00:00.000000000",
        attention_since="2026-08-15 12:00:00.000000000",
    )
    code, out, err = run_cli(
        tmp_path,
        inbox_main,
        ["--color=never"],
        monkeypatch,
        gerrit=_store(base, top),
    )
    assert code == int(ExitCode.ATTENTION), err
    assert "to review (1)" in out
    assert "c4321" in out
    assert "2c" in out
    assert "unrevi" in out
    assert "act" in out
    assert "alice" in out
    assert "feat: rate limiter" in out
    assert "https://gerrit.example.com/c/myproject/+/4321" in out


def test_json_contract_includes_ages_and_top_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    top = _change(
        number=4400,
        sha="ccc",
        parent="origin",
        subject="fix: retry backoff",
        owner="bob",
        updated="2026-08-18 08:00:00.000000000",
        attention_since="2026-08-17 12:00:00.000000000",
        comments=2,
    )
    code, out, err = run_cli(tmp_path, inbox_main, ["--json"], monkeypatch, gerrit=_store(top))
    assert code == int(ExitCode.ATTENTION), err
    data = json_stdout(out)
    assert data["host"] == "gerrit.example.com"
    assert data["sections"][0]["name"] == "to-review"
    chain = data["sections"][0]["chains"][0]
    assert chain["top"]["number"] == 4400
    assert chain["top"]["url"] == "https://gerrit.example.com/c/myproject/+/4400"
    assert chain["unreviewed_age_seconds"] > 0
    assert chain["wait_age_seconds"] >= 0
    assert chain["last_activity"]
    assert "unresolved-comments" in chain["attention_reasons"]
    assert data["summary"]["chains"] == 1


def test_follow_up_commit_query_fills_chain_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    parent = _change(
        number=1,
        sha="aaa",
        parent="origin",
        subject="base",
        owner="bob",
        updated="2026-08-10 12:00:00.000000000",
        attention_since="2026-08-12 12:00:00.000000000",
    )
    child = _change(
        number=2,
        sha="bbb",
        parent="aaa",
        subject="tip",
        updated="2026-08-18 08:00:00.000000000",
        attention_since="2026-08-17 12:00:00.000000000",
    )
    store = _store(parent, child, stub=[child])
    service = GerritService.from_cwd(tmp_path, rest=store, settings=Settings.from_map({}))
    chains = service.fetch_review_chains(TO_REVIEW_QUERY)
    assert len(chains) == 1
    assert chains[0].depth == 2
    assert [member.number for member in chains[0].members] == [1, 2]
    commit_queries = [query for query in store.queries() if "commit:" in query]
    assert commit_queries
    assert any("commit:aaa" in query for query in commit_queries)


def test_does_not_resolve_stack_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Inbox runs outside a clone: no remotes, no triplets, no stack context."""
    top = _change(
        number=9,
        sha="zzz",
        parent="origin",
        subject="outside a repo",
        updated="2026-08-18 08:00:00.000000000",
        attention_since="2026-08-17 12:00:00.000000000",
    )
    code, out, err = run_cli(tmp_path, inbox_main, ["--json"], monkeypatch, gerrit=_store(top))
    assert code == int(ExitCode.ATTENTION), err
    assert json_stdout(out)["sections"][0]["chains"][0]["top"]["number"] == 9


def test_build_to_review_query_folds_verified_and_projects() -> None:
    default = Settings.from_map({})
    assert build_to_review_query(default, projects=[], include_unready=False) == TO_REVIEW_QUERY
    with_project = build_to_review_query(default, projects=["foo"], include_unready=False)
    assert with_project.endswith("project:foo")
    custom = Settings.from_map({"inbox.toReviewQuery": "reviewer:self is:open"})
    assert build_to_review_query(custom, projects=[], include_unready=False) == "reviewer:self is:open"


def test_indents_members_with_attention(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = _change(
        number=4376,
        sha="aaa",
        parent="origin",
        subject="build failed",
        updated="2026-08-12 12:00:00.000000000",
        attention_since="2026-08-12 12:00:00.000000000",
        verified=-1,
    )
    top = _change(
        number=4380,
        sha="bbb",
        parent="aaa",
        subject="refactor: split scheduler",
        owner="carol",
        updated="2026-08-12 12:00:00.000000000",
        attention_since="2026-08-12 12:00:00.000000000",
        verified=-1,
    )
    code, out, err = run_cli(
        tmp_path,
        inbox_main,
        ["--color=never"],
        monkeypatch,
        gerrit=_store(base, top),
    )
    assert code == int(ExitCode.ATTENTION), err
    assert "└ c4376" in out
    assert "build failed" in out
