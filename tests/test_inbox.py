# Spec: docu/spec/commands/inbox.md — to-review overview

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_inbox import main as inbox_main
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.rest import LIGHT_QUERY_OPTIONS, LOG_QUERY_OPTIONS
from gerrit_workflow_tools.core.gerrit.service import GerritService
from gerrit_workflow_tools.core.git_run import git
from gerrit_workflow_tools.core.inbox import (
    DEFAULT_OUTGOING_QUERY,
    DEFAULT_TO_REVIEW_QUERY,
    build_to_review_query,
    project_scope,
)
from gerrit_workflow_tools.core.run_context import RunContext
from gerrit_workflow_tools.render.style import (
    ANSI_BOLD,
    ANSI_CYAN,
    ANSI_DIM,
    ANSI_GREEN,
    ANSI_LIGHT_GREEN,
    ANSI_RED,
    ANSI_RESET,
    ANSI_YELLOW,
    GERRIT_LINK_LABEL,
    strip_ansi,
)
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
    store.stub_query(DEFAULT_OUTGOING_QUERY, [])
    return store


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
    assert code == int(ExitCode.OK), err
    assert "to review (1)" in out
    assert "c4321" in out
    assert "2 changes" in out
    assert "worst v+1" in out  # the chain's worst votes, said so: not the top's own
    assert re.search(r"waiting \d+d, active \d+d", out)
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
    assert code == int(ExitCode.OK), err
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
    assert data["sections"][0]["summary"]["chains"] == 1


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
    service = RunContext.open(tmp_path, gerrit=store, settings=Settings.from_map({})).gerrit
    chains = service.fetch_review_chains(TO_REVIEW_QUERY)
    assert len(chains) == 1
    assert chains[0].depth == 2
    assert [member.number for member in chains[0].members] == [1, 2]
    commit_queries = [query for query in store.queries() if "commit:" in query]
    assert commit_queries
    assert any("commit:aaa" in query for query in commit_queries)


def test_follow_up_commit_query_is_light_only_once_the_parent_is_cached(tmp_path: Path) -> None:
    """The first follow-up asks for the parent in full (nothing to hold a light answer against); the next, lightly."""
    parent = _change(
        number=1, sha="aaa", parent="origin", subject="base", owner="bob", updated="2026-08-10 12:00:00.000000000"
    )
    child = _change(number=2, sha="bbb", parent="aaa", subject="tip", updated="2026-08-18 08:00:00.000000000")
    store = _store(parent, child, stub=[child])
    service = RunContext.open(tmp_path, gerrit=store, settings=Settings.from_map({})).gerrit

    def commit_query_options() -> list[list[str]]:
        before = len(store.calls)
        assert [member.number for member in service.fetch_review_chains(TO_REVIEW_QUERY)[0].members] == [1, 2]
        return [call.kwargs["options"] for call in store.calls[before:] if "commit:" in str(call.args[0])]

    assert commit_query_options() == [list(LOG_QUERY_OPTIONS)]
    assert commit_query_options() == [list(LIGHT_QUERY_OPTIONS)]


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
    assert code == int(ExitCode.OK), err
    assert json_stdout(out)["sections"][0]["chains"][0]["top"]["number"] == 9


def test_build_to_review_query_folds_verified_and_projects() -> None:
    default = Settings.from_map({})
    assert build_to_review_query(default, projects=[], include_unready=False) == TO_REVIEW_QUERY
    with_project = build_to_review_query(default, projects=["foo"], include_unready=False)
    assert with_project.endswith("project:foo")
    custom = Settings.from_map({"ger.inboxToReviewQuery": "reviewer:self is:open"})
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
    assert code == int(ExitCode.OK), err
    assert "└ c4376" in out
    assert "build failed" in out


def _painted(code: str, text: str) -> str:
    return f"{code}{text}{ANSI_RESET}"


def test_inbox_colorizes_identity_status_wait_and_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    top = _change(
        number=4400,
        sha="ccc",
        parent="origin",
        subject="fix: retry backoff",
        owner="bob",
        updated="2026-08-18 08:00:00.000000000",
        attention_since="2026-08-17 12:00:00.000000000",
        comments=2,
        cr=1,
    )
    code, out, err = run_cli(
        tmp_path,
        inbox_main,
        ["--color", "always"],
        monkeypatch,
        gerrit=_store(top),
    )
    assert code == int(ExitCode.OK), err
    assert _painted(f"{ANSI_BOLD}{ANSI_CYAN}", "to review (1)") in out
    assert _painted(ANSI_CYAN, "c4400") in out
    assert _painted(ANSI_DIM, "1 change") in out
    assert _painted(ANSI_GREEN, "v+1") in out
    assert _painted(ANSI_LIGHT_GREEN, "cr+1") in out
    assert _painted(ANSI_YELLOW, "com") in out
    assert _painted(ANSI_DIM, "waiting") in out
    assert _painted(ANSI_DIM, "active") in out
    assert _painted(ANSI_DIM, "bob") in out
    assert _painted(ANSI_DIM, "https://gerrit.example.com/c/myproject/+/4400") in out
    assert _painted(f"{ANSI_BOLD}{ANSI_CYAN}", "summary:") in out
    assert ANSI_YELLOW in out
    assert "\x1b[" not in strip_ansi(out)


def test_inbox_colorizes_attention_notes_like_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        ["--color", "always"],
        monkeypatch,
        gerrit=_store(base, top),
    )
    assert code == int(ExitCode.OK), err
    assert _painted(ANSI_RED, "v-1") in out
    assert _painted(ANSI_RED, "build failed") in out
    assert _painted(ANSI_DIM, "build failed ") in out
    assert _painted(ANSI_RED, "1") in out  # summary CI count
    assert "\x1b[" not in strip_ansi(out)


def test_inbox_hyperlinks_make_the_change_label_the_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    top = _change(
        number=4321,
        sha="bbb",
        parent="origin",
        subject="feat: rate limiter",
        updated="2026-08-18 08:00:00.000000000",
        attention_since="2026-08-15 12:00:00.000000000",
    )
    code, out, err = run_cli(
        tmp_path,
        inbox_main,
        ["--hyperlinks", "always", "--color=never"],
        monkeypatch,
        gerrit=_store(top),
    )
    assert code == int(ExitCode.OK), err
    # The row's c4321 opens the change: no URL line below it, half the height.
    assert "\x1b]8;;https://gerrit.example.com/c/myproject/+/4321\x1b\\c4321\x1b]8;;" in out
    visible = strip_ansi(out)
    assert GERRIT_LINK_LABEL not in visible and "/c/myproject/+/4321" not in visible
    assert visible.splitlines()[3].startswith("c4321")
    assert visible.splitlines()[4:6] == ["", "summary: 1 chains · 1 changes · longest wait 100d"]
    assert "https://gerrit.example.com" not in visible


def test_inbox_hyperlinks_never_keeps_raw_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    top = _change(
        number=4321,
        sha="bbb",
        parent="origin",
        subject="feat: rate limiter",
        updated="2026-08-18 08:00:00.000000000",
        attention_since="2026-08-15 12:00:00.000000000",
    )
    code, out, err = run_cli(
        tmp_path,
        inbox_main,
        ["--hyperlinks", "never", "--color=never"],
        monkeypatch,
        gerrit=_store(top),
    )
    assert code == int(ExitCode.OK), err
    assert "\x1b]8;" not in out
    assert "https://gerrit.example.com/c/myproject/+/4321" in out
    assert GERRIT_LINK_LABEL not in out


def test_inbox_json_keeps_raw_url_with_hyperlinks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    top = _change(
        number=4321,
        sha="bbb",
        parent="origin",
        subject="feat: rate limiter",
        updated="2026-08-18 08:00:00.000000000",
        attention_since="2026-08-15 12:00:00.000000000",
    )
    code, out, err = run_cli(
        tmp_path,
        inbox_main,
        ["--json", "--hyperlinks", "always"],
        monkeypatch,
        gerrit=_store(top),
    )
    assert code == int(ExitCode.OK), err
    data = json_stdout(out)
    assert data["sections"][0]["chains"][0]["top"]["url"] == ("https://gerrit.example.com/c/myproject/+/4321")
    assert GERRIT_LINK_LABEL not in out
    assert "\x1b]8;" not in out


@pytest.mark.parametrize("limit", ["0", "-2"])
def test_inbox_rejects_a_non_positive_limit(limit: str, stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, _out, err = run_cli(stack_repo, inbox_main, ["--limit", limit], monkeypatch, catch_sys_exit=True)
    assert code == ExitCode.USAGE
    assert "--limit" in err


# --- What a listing costs Gerrit (ADR-0013) ----------------------------------------------


def _listing_calls(service: GerritService, store: ChangeStore) -> list[tuple[str, Any]]:
    """Fetch the to-review chains once; the Gerrit calls that took, as ``(method, query or argument)``."""
    before = len(store.calls)
    service.fetch_review_chains(TO_REVIEW_QUERY)
    return [(call.method, call.args[0] if call.args else None) for call in store.calls[before:]]


def _waiting(number: int, sha: str, parent: str) -> dict[str, Any]:
    return _change(
        number=number,
        sha=sha,
        parent=parent,
        subject=f"change {number}",
        updated="2026-08-18 08:00:00.000000000",
        attention_since="2026-08-17 12:00:00.000000000",
    )


def test_a_listing_asked_again_is_one_light_query(tmp_path: Path) -> None:
    """Polled while nothing happens: Gerrit says what matches, the details come from the cache.

    Not asked again: who you are, and the commit the chain sits on (history, never a change).
    """
    store = _store(_waiting(1, "aaa", "history"), _waiting(2, "bbb", "aaa"))
    service = RunContext.open(tmp_path, gerrit=store, settings=Settings.from_map({})).gerrit
    first = _listing_calls(service, store)
    assert ("get_account", "self") in first
    assert any(method == "query_changes" and "commit:history" in query for method, query in first)

    assert _listing_calls(service, store) == [("query_changes", TO_REVIEW_QUERY)]
    assert store.calls[-1].kwargs["options"] == ["SKIP_DIFFSTAT"]
    assert [member.number for member in service.fetch_review_chains(TO_REVIEW_QUERY)[0].members] == [1, 2]


def test_a_listing_fetches_details_only_for_the_change_that_moved(tmp_path: Path) -> None:
    one, two = _waiting(1, "aaa", "history"), _waiting(2, "bbb", "aaa")
    store = _store(one, two)
    service = RunContext.open(tmp_path, gerrit=store, settings=Settings.from_map({})).gerrit
    _listing_calls(service, store)

    moved = dict(two, updated="2026-08-18 09:00:00.000000000", subject="change 2, reworded")
    store.add_change(moved)
    store.stub_query(TO_REVIEW_QUERY, [one, moved])
    assert _listing_calls(service, store) == [("query_changes", TO_REVIEW_QUERY), ("query_changes", "change:2")]
    assert service.fetch_review_chains(TO_REVIEW_QUERY)[0].top.subject == "change 2, reworded"


def test_an_open_parent_outside_the_listing_is_asked_about_every_time(tmp_path: Path) -> None:
    """It is someone's open change: it can get a new patchset, so only history is remembered as settled."""
    parent, child = _waiting(1, "aaa", "history"), _waiting(2, "bbb", "aaa")
    store = _store(parent, child, stub=[child])
    service = RunContext.open(tmp_path, gerrit=store, settings=Settings.from_map({})).gerrit
    _listing_calls(service, store)
    again = _listing_calls(service, store)
    assert again == [("query_changes", TO_REVIEW_QUERY), ("query_changes", "commit:aaa")]


def test_refresh_asks_for_everything_again(tmp_path: Path) -> None:
    store = _store(_waiting(1, "aaa", "history"))
    settings = Settings.from_map({})
    _listing_calls(RunContext.open(tmp_path, gerrit=store, settings=settings).gerrit, store)
    fresh = _listing_calls(RunContext.open(tmp_path, gerrit=store, settings=settings, refresh=True).gerrit, store)
    # The listing, its details, who you are, and the commit below the chain.
    assert sorted(method for method, _ in fresh) == ["get_account", "query_changes", "query_changes", "query_changes"]


def test_what_a_chain_sits_on_is_remembered_by_how_final_it_is(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A merged change stays merged; a commit Gerrit shows no change for may be one you cannot see yet."""
    clock = {"t": 1_000_000}
    monkeypatch.setattr("gerrit_workflow_tools.core.gerrit.cache._now", lambda: clock["t"])
    merged = dict(_waiting(1, "aaa", "root"), status="MERGED")
    on_merged, on_unknown = _waiting(2, "bbb", "aaa"), _waiting(3, "ccc", "history")
    store = _store(merged, on_merged, on_unknown, stub=[on_merged, on_unknown])
    service = RunContext.open(tmp_path, gerrit=store, settings=Settings.from_map({})).gerrit

    def commits_asked() -> list[str]:
        return sorted(query for method, query in _listing_calls(service, store) if "commit:" in str(query))

    assert len(commits_asked()) == 1  # both parents, in one query
    assert commits_asked() == []
    clock["t"] += 16 * 60
    assert commits_asked() == ["commit:history"]
    clock["t"] += 8 * 24 * 60 * 60
    (both,) = commits_asked()
    assert "commit:aaa" in both and "commit:history" in both


def _in_clone(path: Path, project: str) -> Path:
    git("init", "-q", cwd=path)
    git("remote", "add", "origin", f"https://gerrit.example.com/{project}", cwd=path)
    return path


def test_scope_inside_a_clone_is_its_project(tmp_path: Path) -> None:
    clone = _in_clone(tmp_path, "myproject")
    settings = Settings.from_cwd(clone)
    assert project_scope(clone, settings) == ["myproject"]
    assert project_scope(clone, settings, all_projects=True) == []
    assert project_scope(clone, settings, projects=["other"]) == ["other"]


def test_scope_outside_a_clone_is_the_configured_projects(tmp_path: Path) -> None:
    assert project_scope(tmp_path, Settings.from_map({})) == []
    assert project_scope(tmp_path, Settings.from_map({"ger.inboxProjects": "a,b"})) == ["a", "b"]


def _mine(number: int, project: str) -> dict[str, Any]:
    row = _change(
        number=number,
        sha=f"m{number}",
        parent="origin",
        subject=f"mine in {project}",
        owner="me",
        updated="2026-08-18 08:00:00.000000000",
    )
    row.update(project=project, id=f"{project}~main~{row['change_id']}")
    return row


def test_inside_a_clone_both_sections_are_its_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clone = _in_clone(tmp_path, "myproject")
    theirs = _change(
        number=7,
        sha="ttt",
        parent="origin",
        subject="theirs",
        updated="2026-08-18 08:00:00.000000000",
        attention_since="2026-08-17 12:00:00.000000000",
    )
    store = _store(theirs, _mine(8, "myproject"))
    store.stub_query(f"{TO_REVIEW_QUERY} project:myproject", [theirs])
    store.stub_query(f"{DEFAULT_OUTGOING_QUERY} project:myproject", [_mine(8, "myproject")])
    code, out, err = run_cli(clone, inbox_main, ["--json"], monkeypatch, gerrit=store)
    assert code == int(ExitCode.OK), err
    data = json_stdout(out)
    assert data["projects"] == ["myproject"]
    to_review, outgoing = data["sections"]
    assert to_review["query"].endswith("project:myproject")
    assert [c["top"]["number"] for c in to_review["chains"]] == [7]
    assert outgoing["name"] == "outgoing"
    assert [c["top"]["number"] for c in outgoing["chains"]] == [8]


def test_all_drops_the_project_filter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clone = _in_clone(tmp_path, "myproject")
    store = _store(_mine(8, "myproject"), _mine(9, "elsewhere"), stub=[])
    store.stub_query(DEFAULT_OUTGOING_QUERY, [_mine(8, "myproject"), _mine(9, "elsewhere")])
    argv = ["--all", "--outgoing", "-v", "--color=never"]
    code, out, err = run_cli(clone, inbox_main, argv, monkeypatch, gerrit=store)
    assert code == int(ExitCode.OK), err
    assert "project: all projects" in out
    assert "outgoing (2; none need you)" in out
    assert "to review" not in out
    assert "mine in elsewhere" in out
    assert TO_REVIEW_QUERY not in store.queries()


def test_to_review_alone_does_not_ask_for_outgoing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(stub=[])
    code, out, err = run_cli(tmp_path, inbox_main, ["--to-review", "--color=never"], monkeypatch, gerrit=store)
    assert code == int(ExitCode.OK), err
    assert "to review (0)" in out
    assert "outgoing" not in out
    assert TO_REVIEW_QUERY in store.queries()
    assert DEFAULT_OUTGOING_QUERY not in store.queries()


def test_all_and_project_do_not_combine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, _out, _err = run_cli(tmp_path, inbox_main, ["--all", "--project", "x"], monkeypatch, catch_sys_exit=True)
    assert code == int(ExitCode.USAGE)


def _ai_inbox() -> ChangeStore:
    """To review: a healthy chain of one, and carol's chain of two with failed CI and comments.

    Outgoing: one change of mine, with failed CI.
    """

    def change(number: int, sha: str, parent: str, subject: str, **fields: Any) -> dict[str, Any]:
        when = "2026-08-12 12:00:00.000000000"
        return _change(
            number=number, sha=sha, parent=parent, subject=subject, updated=when, attention_since=when, **fields
        )

    healthy = change(4321, "hhh", "origin", "feat: rate limiter", cr=1)
    base = change(4376, "aaa", "origin", "split part one", verified=-1)
    top = change(4380, "bbb", "aaa", "refactor: split scheduler", owner="carol", comments=2)
    mine = change(4410, "mmm", "origin", "feat: quota headers", owner="me", verified=-1)
    store = _store(healthy, base, top, mine, stub=[healthy, base, top])
    store.stub_query(DEFAULT_OUTGOING_QUERY, [mine])
    return store


def test_inbox_ai_is_one_plain_line_per_chain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``--ai``: change numbers, not URLs; the human view's hint words; a fact only where it has a value."""
    code, out, err = run_cli(tmp_path, inbox_main, ["--ai", "--color", "always"], monkeypatch, gerrit=_ai_inbox())
    assert code == int(ExitCode.OK), err
    assert out == strip_ansi(out)  # --ai forces colour off, whatever --color says
    assert err == strip_ansi(err)
    lines = [re.sub(r"\b\d+[mhd]\b", "<age>", line) for line in out.splitlines()]
    assert lines[0] == "project: all projects"
    assert lines[1] == "to review: 2 chains, 3 changes"
    assert sorted(lines[2:4]) == [
        "change 4321 feat: rate limiter — by alice, waiting on you <age>, last activity <age>, V+1, CR+1",
        "change 4380 refactor: split scheduler — 2 changes, by carol, waiting on you <age>, "
        "last activity <age>, V-1, 4376: build failed, 4380: 2 unresolved comments",
    ]
    assert lines[4:] == [
        "outgoing: 1 chain, 1 change",
        "change 4410 feat: quota headers — last activity <age>, V-1, 4410: build failed",  # no owner, no wait on you
    ]
    for absent in ("http", "c4321", "CR0", "0 unresolved", "None", "#"):
        assert absent not in out


def test_inbox_ai_is_far_smaller_than_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _code, ai, _err = run_cli(tmp_path, inbox_main, ["--ai"], monkeypatch, gerrit=_ai_inbox())
    _code, as_json, _err = run_cli(tmp_path, inbox_main, ["--json"], monkeypatch, gerrit=_ai_inbox())
    assert len(ai) * 5 < len(as_json)


# --- Rows a new user reads at a glance ---------------------------------------------


def _subject_columns(out: str) -> set[int]:
    return {line.index("# ") for line in out.splitlines() if line.startswith("c4")}


def test_rows_line_up_whatever_the_owner_and_ages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        _change(
            number=4401, sha="a1", parent="origin", subject="one", owner="al", updated="2026-08-18 08:00:00.000000000"
        ),
        _change(
            number=4402,
            sha="a2",
            parent="origin",
            subject="two",
            owner="Bartholomew Reviewer",
            updated="2026-01-18 08:00:00.000000000",
            attention_since="2026-08-18 07:00:00.000000000",
        ),
    ]
    code, out, err = run_cli(tmp_path, inbox_main, ["--color=never", "--no-url"], monkeypatch, gerrit=_store(*rows))
    assert code == int(ExitCode.OK), err
    assert len(_subject_columns(out)) == 1, out


def test_a_terminal_cuts_the_subject_to_fit_and_a_pipe_never_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    subject = "docs: " + "a very long subject " * 6
    row = _change(number=4410, sha="t1", parent="origin", subject=subject, updated="2026-08-18 08:00:00.000000000")
    code, out, _err = run_cli(tmp_path, inbox_main, ["--color=never", "--no-url"], monkeypatch, gerrit=_store(row))
    assert code == int(ExitCode.OK) and subject in out
    monkeypatch.setattr("gerrit_workflow_tools.render.inbox_text.terminal_columns", lambda: 100)
    code, out, _err = run_cli(tmp_path, inbox_main, ["--color=never", "--no-url"], monkeypatch, gerrit=_store(row))
    [line] = [line for line in out.splitlines() if line.startswith("c4410")]
    assert line.endswith("…") and len(line) < 100


def test_to_review_says_how_many_chains_it_leaves_out_as_not_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ready = _change(number=4420, sha="r1", parent="origin", subject="ready", updated="2026-08-18 08:00:00.000000000")
    unready = _change(
        number=4421, sha="r2", parent="origin", subject="no CI yet", updated="2026-08-18 08:00:00.000000000"
    )
    store = _store(ready, unready, stub=[ready])
    store.stub_query(
        build_to_review_query(Settings.from_cwd(tmp_path), projects=[], include_unready=True), [ready, unready]
    )
    code, out, err = run_cli(tmp_path, inbox_main, ["--color=never"], monkeypatch, gerrit=store)
    assert code == int(ExitCode.OK), err
    assert "to review (1; 1 not ready, --unready shows it)" in out
    assert "no CI yet" not in out


def test_outgoing_lists_what_needs_you_first_and_folds_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    quiet = _mine(4430, "myproject")
    failed = {**_mine(4431, "myproject"), "subject": "red build"}
    failed["labels"] = {"Verified": {"value": -1, "all": [{"value": -1}]}, "Code-Review": {"value": 0, "all": []}}
    wip = {**_mine(4432, "myproject"), "subject": "half done", "work_in_progress": True}
    private = {**_mine(4433, "myproject"), "subject": "secret", "is_private": True, "work_in_progress": True}
    store = _store(quiet, failed, wip, private, stub=[])
    store.stub_query(DEFAULT_OUTGOING_QUERY, [quiet, failed, wip, private])
    code, out, err = run_cli(tmp_path, inbox_main, ["--outgoing", "--color=never"], monkeypatch, gerrit=store)
    assert code == int(ExitCode.OK), err
    assert "outgoing (4; 3 need you)" in out
    rows = [line for line in out.splitlines() if line.startswith("c4")]
    assert rows[0].startswith("c4431")  # failed CI first
    assert any(r.startswith("c4432") and r.endswith("# half done  # WIP") for r in rows)
    assert any(r.endswith("# secret  # WIP, private") for r in rows)
    assert "mine in myproject" not in out
    assert "+ 1 chain waits on reviewers or CI (-v lists them)" in out
    code, out, err = run_cli(tmp_path, inbox_main, ["--outgoing", "-v", "--color=never"], monkeypatch, gerrit=store)
    assert "mine in myproject" in out and "-v lists" not in out


def test_the_footer_names_the_command_that_starts_the_longest_waiting_chain_at_its_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = _change(number=4440, sha="b1", parent="origin", subject="base", updated="2026-08-18 08:00:00.000000000")
    top = _change(number=4441, sha="b2", parent="b1", subject="top", updated="2026-08-18 08:00:00.000000000")
    code, out, err = run_cli(tmp_path, inbox_main, ["--color=never"], monkeypatch, gerrit=_store(base, top))
    assert code == int(ExitCode.OK), err
    assert "next: ger review c4441 --bottom, then --next" in out


def test_a_chain_holding_drafts_of_yours_says_so_with_one_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drafts nobody sees until published: the row names the change they are on (one has:draft query)."""
    base = _change(number=4450, sha="d1", parent="origin", subject="base", updated="2026-08-18 08:00:00.000000000")
    top = _change(number=4451, sha="d2", parent="d1", subject="top", updated="2026-08-18 08:00:00.000000000")
    store = _store(base, top)
    store.set_drafts(str(base["id"]), {"a.py": [{"id": "d", "line": 1, "message": "hm", "patch_set": 1}]})
    code, out, err = run_cli(tmp_path, inbox_main, ["--color=never"], monkeypatch, gerrit=store)
    assert code == int(ExitCode.OK), err
    [row] = [line for line in out.splitlines() if line.startswith("c4451")]
    assert row.endswith("# top  # drafts of yours on c4450")
    assert [q for q in store.queries() if "has:draft" in q] == ["has:draft"]
