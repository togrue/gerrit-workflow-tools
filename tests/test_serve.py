# Spec: docu/spec/commands/serve.md
"""``ger serve``: the API through ``GerApi.handle``, the transport through a real socket."""

from __future__ import annotations

import dataclasses
import io
import json
import logging
import re
import socket
import struct
import sys
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from itertools import pairwise
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_log import main as log_main
from gerrit_workflow_tools.cli_serve import main as serve_main
from gerrit_workflow_tools.cli_show import main as show_main
from gerrit_workflow_tools.core import call_trace, reorder, reorder_preview
from gerrit_workflow_tools.core.ci_content import CiContent, CiFinding, CiProvenance, introduced_fingerprints
from gerrit_workflow_tools.core.ci_links import CiLink
from gerrit_workflow_tools.core.commit_diff import commit_diff, show_commit_diff
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.stack_context import ChangeResolutionError
from gerrit_workflow_tools.core.gerrit_patchset import changes_ref
from gerrit_workflow_tools.core.git_run import UnknownRevisionError, _run_git, git, git_out
from gerrit_workflow_tools.core.inbox import DEFAULT_OUTGOING_QUERY, build_to_review_query
from gerrit_workflow_tools.core.rebase_state import rebase_in_progress
from gerrit_workflow_tools.core.reorder import ReorderRefusal, StackStep, rewrite_stack
from gerrit_workflow_tools.core.reorder_preview import PickConflict, PreviewUnavailable, first_conflict, swap_conflicts
from gerrit_workflow_tools.core.review_checkout import ReviewChainEndError
from gerrit_workflow_tools.core.review_delta import patchset_delta_files
from gerrit_workflow_tools.core.stack import Commit, read_commit_meta
from gerrit_workflow_tools.server.api import GerApi
from gerrit_workflow_tools.server.httpd import GerHttpServer, _Handler, failure_response
from gerrit_workflow_tools.server.request import BadRequest, NoRoute
from tests.change_store import ChangeStore
from tests.ci_fakes import JENKINS, FakeCiHttp, jenkins_build
from tests.cli_gerrit_mocks import build_details_by_change_id, stack_rows_mb_to_head, web_edit
from tests.conftest import json_stdout, run_cli
from tests.test_suggested_edits import BLOCK_COMMENT, FIX_COMMENT


WEB = "https://g.example"
OUTGOING_QUERY = f"{DEFAULT_OUTGOING_QUERY} project:testproj"
V1 = "/api/v1"


def _build_failed(url: str = JENKINS) -> dict[str, Any]:
    return {
        "message": f"Patch Set 1: Verified-1\n\nBuild Failed \n\n{url}/ : FAILURE",
        "_revision_number": 1,
        "date": "2026-11-09 10:00:00",
    }


def _gerrit(repo: Path, overrides: list[dict[str, Any]] | None = None) -> tuple[ChangeStore, list[str], list[Commit]]:
    """The local stack as changes 100-103 of one review chain on Gerrit; both inbox queries answer.

    Returns the store, the triplets and the stack rows, base first. Changes with Verified -1
    carry a Jenkins ``Build Failed`` message. *Outgoing* holds the whole stack, *to review* nothing.
    """
    rows = stack_rows_mb_to_head(repo)
    overrides = overrides or [{} for _ in rows]
    details = build_details_by_change_id(rows, per_index_overrides=overrides)
    keys = list(details)
    parent = git_out("rev-parse", f"{rows[0].sha}^", cwd=repo)
    for key, row in zip(keys, rows, strict=True):
        detail = details[key]
        detail["subject"] = row.subject
        detail["owner"] = {"name": "me", "email": "me@example.com", "_account_id": 1000}
        detail["updated"] = detail["created"] = "2026-11-09 10:00:00.000000000"
        detail["revisions"][row.sha].update(
            {"created": detail["created"], "commit": {"parents": [{"commit": parent}], "subject": row.subject}}
        )
        parent = row.sha
    failed = {key: [_build_failed()] for key, ov in zip(keys, overrides, strict=True) if ov.get("verified") == -1}
    store = ChangeStore(
        details,
        web_base=WEB,
        checks={key: [] for key in keys},
        messages={key: failed.get(key, []) for key in keys},
        accounts={1000: {"_account_id": 1000, "username": "me", "name": "me"}},
        self_account_id=1000,
    )
    settings = Settings.from_cwd(repo)
    store.stub_query(build_to_review_query(settings, projects=["testproj"], include_unready=False), [])
    store.stub_query(OUTGOING_QUERY, list(details.values()))
    related = [{"_change_number": 100 + i, "_revision_number": 1, "status": "NEW"} for i in range(len(rows))]
    for key in keys:
        store.set_related(key, list(reversed(related)))
    return store, keys, rows


def _get(api: GerApi, endpoint: str, /, **query: str | list[str]) -> dict[str, Any]:
    params = {name: value if isinstance(value, list) else [value] for name, value in query.items()}
    unlocked = api.handle_unlocked("GET", f"{V1}{endpoint}", params)
    return unlocked if unlocked is not None else api.handle("GET", f"{V1}{endpoint}", params)


def _post(api: GerApi, path: str, body: dict[str, Any]) -> dict[str, Any]:
    return api.handle("POST", f"{V1}{path}", {}, body)


# --- Overview and chains -----------------------------------------------------------


def test_stack_is_the_annotated_stack_with_the_ready_boundary(stack_repo: Path) -> None:
    store, _keys, rows = _gerrit(stack_repo, [{}, {"unresolved_comment_count": 2}, {}, {}])
    stack = _get(GerApi(stack_repo, gerrit=store), "/stack")
    assert stack["branch"] == "feature"
    assert stack["stack"] == {"project": "testproj", "target_branch": "origin/main", "push_branch": "main"}
    assert [c["sha"] for c in stack["commits"]] == [row.sha for row in rows]
    assert [c["is_head"] for c in stack["commits"]] == [False, False, False, True]
    # `test! temporary experiment` is the ready boundary: it and what sits above are held back.
    assert [c["held_back"] for c in stack["commits"]] == [False, False, True, True]
    assert stack["summary"] == {"commits": 4, "ready": 2, "attention": 1, "needs_edit": 1, "not_on_gerrit": 0}
    second = stack["commits"][1]
    assert second["comments_unresolved"] == 2 and second["needs_edit"] is True
    assert "unresolved-comments" in second["attention_reasons"]
    assert second["gerrit_url"] == f"{WEB}/c/testproj/+/101"
    assert [c["number"] for c in stack["commits"]] == [100, 101, 102, 103]
    assert [c["current_patch_set"] for c in stack["commits"]] == [1] * 4, "the number the Change view names"
    assert [c["last_pushed_votes"] for c in stack["commits"]] == [None] * 4, "in step with Gerrit: votes are current"
    assert [c["code_change"] for c in stack["commits"]] == [None] * 4, "only a commit ahead of Gerrit has one"


def test_stack_local_answers_from_git_alone_without_asking_gerrit(stack_repo: Path) -> None:
    """``local=1``: the same commits at once, every Gerrit state unknown, Gerrit never asked."""
    _store, _keys, rows = _gerrit(stack_repo)
    stack = _get(GerApi(stack_repo, gerrit=_Refusing()), "/stack", local="1")
    assert stack["local_only"] is True
    assert [c["sha"] for c in stack["commits"]] == [row.sha for row in rows]
    assert {c["patchset_status"] for c in stack["commits"]} == {"unknown"}
    assert "gerrit_unavailable" not in stack
    assert [c["held_back"] for c in stack["commits"]] == [False, False, True, True], "the ready boundary is git's"


def test_show_says_why_gerrit_state_is_unknown_when_gerrit_refuses(stack_repo: Path) -> None:
    """Refused credentials: the commit carries the reason, so a viewer says unknown, never none."""
    from gerrit_workflow_tools.core.gerrit.rest import GerritUnavailableError

    _gerrit(stack_repo)
    refused = "Gerrit at g.example rejected the credentials (HTTP 401)"

    class _Rejecting:
        web_base = WEB

        def __getattr__(self, name: str) -> Any:
            def _fail(*_args: object, **_kwargs: object) -> Any:
                raise GerritUnavailableError(refused, status=401, reason="credentials")

            return _fail

    shown = _get(GerApi(stack_repo, gerrit=_Rejecting()), "/show", rev="HEAD")["commits"][0]
    assert shown["gerrit_unavailable"] == {"code": "GERRIT", "message": refused, "reason": "credentials"}
    assert shown["patchset_status"] == "unknown"


def test_stack_says_before_the_press_that_review_here_would_refuse_over_edits(stack_repo: Path) -> None:
    """ger.reviewCheckout=branch and a changed tracked file: /stack names it, as ger review would refuse."""
    store, _keys, _rows = _gerrit(stack_repo)
    api = GerApi(stack_repo, gerrit=store)
    assert _get(api, "/stack")["review_blocked"] is None
    (stack_repo / "a.txt").write_text("edited\n")
    assert _get(api, "/stack")["review_blocked"] is None, "a review worktree is not in the way of edits here"
    git("config", "ger.reviewCheckout", "branch", cwd=stack_repo)
    blocked = _get(api, "/stack")["review_blocked"]
    assert (blocked["reason"], blocked["files"]) == ("uncommitted-changes", ["a.txt"])
    assert blocked["message"].startswith("your working tree has changes to tracked files (a.txt)")


class _Refusing:
    """A Gerrit that fails the test when asked anything."""

    web_base = WEB

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"Gerrit was asked ({name})")


def test_ger_log_json_and_stack_keep_their_keys_and_agree(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Both answer from one stack overview: the keys each has today, and the same commits and boundary."""
    store, _keys, rows = _gerrit(stack_repo)
    code, out, _err = run_cli(stack_repo, log_main, ["--json"], monkeypatch, gerrit=store)
    log = json_stdout(out)
    stack = _get(GerApi(stack_repo, gerrit=store), "/stack")
    assert code == ExitCode.OK
    assert list(log) == ["stack", "rebase", "upstream", "commits", "next"]
    assert list(stack) == [
        *("branch", "head", "rebase_in_progress", "rebase", "upstream", "push_blocked", "staged_review"),
        *("review_blocked", "last_outcome", "stack"),
        *("summary", "commits"),
        "next",
    ]
    assert list(stack["summary"]) == ["commits", "ready", "attention", "needs_edit", "not_on_gerrit"]
    shared = [
        *("sha", "change_id", "summary", "pushed", "patchset_status", "verified", "code_review"),
        *("comments_unresolved", "comments_awaiting_reply", "comments_drafted", "comments_to_answer", "comments_total"),
        *("ci_failures", "ci_links", "ci_pipelines"),
        *(
            "gerrit_url",
            "number",
            "current_patch_set",
            "last_pushed_votes",
            "code_change",
            "ci_patch_set",
            "submittable",
            "votes",
            "reviewers",
            "work_in_progress",
        ),
        *("private", "attention_reasons", "severity", "change_status", "merged_equivalent", "blocks_chain"),
    ]
    assert list(log["commits"][0]) == [*shared, "abandoned", "rebase"]
    assert list(stack["commits"][0]) == [
        *shared,
        *("short_sha", "abandoned", "diverged", "merge", "pinned", "rebase", "updated", "is_head", "held_back"),
        "adds_change_id",
        *("needs_edit", "fixup_of"),
    ]
    assert (log["stack"], log["upstream"], log["rebase"]) == (stack["stack"], stack["upstream"], stack["rebase"])
    assert log["next"] == stack["next"]
    assert [c["sha"] for c in log["commits"]] == [c["sha"] for c in stack["commits"]] == [r.sha for r in rows]
    assert [c["held_back"] for c in stack["commits"]] == [False, False, True, True]


def test_stack_says_how_far_the_upstream_moved_and_what_rebasing_onto_it_would_meet(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``upstream`` is ``ger log --json``'s; no word of it while a rebase is underway (that one comes first)."""
    store, _keys, rows = _gerrit(stack_repo)
    api = GerApi(stack_repo, gerrit=store)
    quiet = {"argv": ["rebase", "origin/main"], "preview": True, "reason": None, "dropped": []}
    assert _get(api, "/stack")["upstream"] == {"name": "origin/main", "behind": 0, "conflict": None, **quiet}

    who = {"GIT_COMMITTER_NAME": "alice", "GIT_COMMITTER_EMAIL": "a@example.com"}
    who |= {"GIT_AUTHOR_NAME": "alice", "GIT_AUTHOR_EMAIL": "a@example.com"}
    index = {"GIT_INDEX_FILE": str(stack_repo / ".git" / "upstream-index")}
    git("read-tree", "origin/main", cwd=stack_repo, env=index)
    blob = git("hash-object", "-w", "--stdin", cwd=stack_repo, input="file a.txt, from main\n").stdout.strip()
    git("update-index", "--add", "--cacheinfo", f"100644,{blob},a.txt", cwd=stack_repo, env=index)
    tree = git("write-tree", cwd=stack_repo, env=index).stdout.strip()
    moved_on = git(
        "commit-tree", tree, "-p", "origin/main", "-m", "Add a.txt on main", cwd=stack_repo, env=who
    ).stdout.strip()
    git("update-ref", "refs/remotes/origin/main", moved_on, cwd=stack_repo)
    conflict = {"sha": rows[0].sha, "summary": "Refactor parser init", "files": ["a.txt"], "kind": "conflict"}
    stack = _get(api, "/stack")
    assert stack["upstream"] == {"name": "origin/main", "behind": 1, "conflict": conflict, **quiet}
    _code, out, _err = run_cli(stack_repo, log_main, ["--json"], monkeypatch, gerrit=store)
    assert json.loads(out)["upstream"] == stack["upstream"], "one model, two shells"

    # Every refresh simulates it again (attributes may have changed, ADR-0014), at the same cost.
    with patch("gerrit_workflow_tools.core.git_run._run_git", wraps=_run_git) as run:
        assert _get(api, "/stack")["upstream"] == stack["upstream"]
    simulated = [
        c.args[0] for c in run.call_args_list if c.args[0] in ("merge-tree", "commit-tree") or "--cherry-pick" in c.args
    ]
    assert simulated == ["rev-list", "merge-tree"], "one cherry-pick walk, one merge up to the conflict"

    assert git("rebase", "origin/main", cwd=stack_repo, env=who, check=False).returncode != 0
    stack = _get(api, "/stack")
    assert stack["rebase_in_progress"] and stack["upstream"] is None


def test_refresh_rereads_the_local_stack_inside_the_trust_window(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``?refresh=1`` (the extension's Refresh) and ``ger --refresh log`` distrust the cache (ADR-0001)."""
    from gerrit_workflow_tools.cli_ger import main as ger_main

    store, keys, rows = _gerrit(stack_repo)
    api = GerApi(stack_repo, gerrit=store)

    def _cr(payload: dict[str, Any]) -> list[Any]:
        return [commit["code_review"] for commit in payload["commits"]]

    def _vote(index: int, value: int) -> None:
        """A vote on Gerrit, which moves the change's ``updated`` (what the refresh probe compares)."""
        [voted] = build_details_by_change_id(rows[index : index + 1], per_index_overrides=[{"cr": value}]).values()
        stamp = f"2026-11-09 10:0{index + 1}:00.000000000"
        store.add_change({**store.get_change(keys[index]), "labels": voted["labels"], "updated": stamp})

    _get(api, "/overview")
    _vote(0, -2)
    assert _cr(_get(api, "/stack"))[0] == 2, "inside the trust window the cache answers"
    assert _cr(_get(api, "/overview", refresh="1")["local"])[0] == -2

    _vote(1, -1)
    assert _cr(_get(api, "/stack"))[1] == 2
    assert _cr(_get(api, "/stack", refresh="1"))[1] == -1

    _vote(2, -1)
    monkeypatch.setattr("gerrit_workflow_tools.core.gerrit.service.HttpGerritRest.from_settings", lambda _s: store)
    _code, out, _err = run_cli(stack_repo, ger_main, ["log", "--json"], monkeypatch)
    assert [c["code_review"] for c in json_stdout(out)["commits"]][:3] == [-2, -1, 2]
    monkeypatch.setenv("GER_CACHE_REFRESH", "")  # `ger --refresh` sets it for the process: restore it after
    _code, out, _err = run_cli(stack_repo, ger_main, ["--refresh", "log", "--json"], monkeypatch)
    assert [c["code_review"] for c in json_stdout(out)["commits"]][:3] == [-2, -1, -1]


def test_overview_joins_both_sections_the_headline_and_the_local_stack(stack_repo: Path) -> None:
    store, _keys, rows = _gerrit(stack_repo, [{}, {"unresolved_comment_count": 1}, {"verified": -1}, {}])
    overview = _get(GerApi(stack_repo, gerrit=store), "/overview")
    to_review, outgoing = overview["sections"]
    assert (to_review["name"], to_review["chains"]) == ("to-review", [])
    assert outgoing["name"] == "outgoing" and outgoing["query"] == OUTGOING_QUERY
    [chain] = outgoing["chains"]
    assert [m["number"] for m in chain["members"]] == [100, 101, 102, 103]
    assert chain["top"]["number"] == 103 and chain["depth"] == 4
    # Members that are commits of the local stack say which.
    assert chain["local"] is True
    assert [m["local_sha"] for m in chain["members"]] == [row.sha for row in rows]
    # And every member names its current patch set, local or not.
    assert [m["sha"] for m in chain["members"]] == [row.sha for row in rows]
    # Within the chain, the headline is where the attention walk stops first: the oldest change
    # needing an edit (101's comments), not the failed CI above it, as ger log names the blocker.
    headline = overview["headline"]
    assert (headline["section"], headline["reason"], headline["number"]) == ("outgoing", "unresolved-comments", 101)
    assert headline["text"] == "1 unresolved comment on 101: Extract command routing"
    assert overview["local"]["summary"]["commits"] == 4
    assert next(c for c in overview["local"]["commits"] if c["needs_edit"])["sha"] == rows[1].sha


def test_the_headline_names_the_oldest_blocker_with_its_own_most_urgent_reason(stack_repo: Path) -> None:
    """The walk's first stop failed CI and has comments: CI it is, there; across chains CI still ranks first."""
    both = {"verified": -1, "unresolved_comment_count": 1}
    store, _keys, _rows = _gerrit(stack_repo, [{}, both, {"unresolved_comment_count": 1}, {}])
    headline = _get(GerApi(stack_repo, gerrit=store), "/overview")["headline"]
    assert (headline["reason"], headline["number"]) == ("ci-failed", 101)
    store, _keys, _rows = _gerrit(stack_repo, [{"cr": -1}, {}, {"verified": -1}, {}])
    headline = _get(GerApi(stack_repo, gerrit=store), "/overview", refresh="1")["headline"]
    assert (headline["reason"], headline["number"]) == ("review-issues", 100)


def test_your_own_push_or_vote_is_the_chains_last_activity_and_no_news(stack_repo: Path) -> None:
    """Whose the last update was, as far as Gerrit tells: a patchset you uploaded, a vote you gave."""

    def last_activity_yours(update: dict[str, Any]) -> bool:
        store, keys, rows = _gerrit(stack_repo)
        top = store._changes[keys[3]]  # pylint: disable=protected-access
        top["updated"] = "2026-11-10 09:00:00.000000000"
        top["revisions"][rows[3].sha].update(created=top["updated"], uploader={"_account_id": 1000})
        top.update(update)
        [chain] = _get(GerApi(stack_repo, gerrit=store), "/overview", refresh="1")["sections"][1]["chains"]
        return bool(chain["last_activity_yours"])

    assert last_activity_yours({}), "your push"
    reviewed = {"_account_id": 2000, "value": 1, "date": "2026-11-10 10:00:00.000000000"}
    later = {"updated": reviewed["date"], "labels": {"Code-Review": {"value": 1, "all": [reviewed]}}}
    assert not last_activity_yours(later), "a reviewer's vote after it is news"
    assert last_activity_yours(
        {**later, "labels": {"Code-Review": {"value": 1, "all": [{**reviewed, "_account_id": 1000}]}}}
    )
    assert not last_activity_yours({"updated": "2026-11-10 11:00:00.000000000"}), (
        "a comment by anyone: Gerrit does not say whose"
    )


def test_a_chain_member_carries_your_own_votes_on_its_current_patchset(stack_repo: Path) -> None:
    """What the task header marks *voted by you*: your votes only, not a reviewer's."""
    store, keys, _rows = _gerrit(stack_repo)
    bob = {"_account_id": 2000, "value": -1}
    you = {"_account_id": 1000, "value": 1}
    store._changes[keys[2]]["labels"] = {"Code-Review": {"value": -1, "all": [bob, you]}}  # pylint: disable=protected-access
    store._changes[keys[3]]["labels"] = {"Code-Review": {"value": -1, "all": [bob]}}  # pylint: disable=protected-access
    [chain] = _get(GerApi(stack_repo, gerrit=store), "/overview", refresh="1")["sections"][1]["chains"]
    assert [m["your_votes"] for m in chain["members"]] == [{}, {}, {"Code-Review": 1}, {}]


def test_reviewed_files_are_gerrits_flag_one_request_each_way(stack_repo: Path) -> None:
    """Files seen in the Change view: Gerrit's per-file flag of one patchset, read and set by number."""
    store, _keys, _rows = _gerrit(stack_repo)
    api = GerApi(stack_repo, gerrit=store)
    assert _get(api, "/reviewed", change="101", patchset="1") == {"change": 101, "patch_set": 1, "reviewed": []}
    marked = _post(api, "/reviewed", {"change": 101, "patchset": 1, "path": "b.txt"})
    assert marked == {"change": 101, "patch_set": 1, "path": "b.txt", "reviewed": True}
    _post(api, "/reviewed", {"change": 101, "patchset": 1, "path": "a.txt", "reviewed": True})
    _post(api, "/reviewed", {"change": 101, "patchset": 1, "path": "b.txt", "reviewed": False})
    store.calls.clear()
    assert _get(api, "/reviewed", change="101", patchset="1")["reviewed"] == ["a.txt"]
    assert [call.method for call in store.calls] == ["list_reviewed_files"], "nothing resolved: one request"
    with pytest.raises(BadRequest):
        _get(api, "/reviewed", change="101")


def test_reviewed_marks_several_files_in_one_request(stack_repo: Path) -> None:
    """Mark all seen: one request here, one to Gerrit per path; a bad path changes nothing."""
    store, _keys, _rows = _gerrit(stack_repo)
    api = GerApi(stack_repo, gerrit=store)
    marked = _post(api, "/reviewed", {"change": 101, "patchset": 1, "paths": ["b.txt", "a.txt", "b.txt"]})
    assert marked == {"change": 101, "patch_set": 1, "paths": ["b.txt", "a.txt"], "reviewed": True}
    assert [call.method for call in store.calls].count("set_file_reviewed") == 2
    assert _get(api, "/reviewed", change="101", patchset="1")["reviewed"] == ["a.txt", "b.txt"]
    store.calls.clear()
    for body in ({"paths": ["a.txt", "-x"]}, {"paths": [1]}, {"path": "a.txt", "paths": ["b.txt"]}, {}):
        with pytest.raises(BadRequest):
            _post(api, "/reviewed", {"change": 101, "patchset": 1, **body})
    assert not [call for call in store.calls if call.method == "set_file_reviewed"], "nothing set"


def test_file_reads_an_object_beside_the_request_being_served(stack_repo: Path) -> None:
    """`/file` is not serialised: a checkout cannot change what a commit id names."""
    store, _keys, rows = _gerrit(stack_repo)
    api = GerApi(stack_repo, gerrit=store)
    answer = api.handle_unlocked("GET", f"{V1}/file", {"rev": [rows[0].sha], "path": ["a.txt"]})
    assert answer is not None and answer["content"] == "file a.txt\n"
    with pytest.raises(NoRoute):
        api.handle("GET", f"{V1}/file", {"rev": [rows[0].sha], "path": ["a.txt"]})


def test_failed_ci_is_the_headline_only_on_the_local_stacks_chain(stack_repo: Path) -> None:
    store, _keys, _rows = _gerrit(stack_repo, [{}, {"unresolved_comment_count": 1}, {"verified": -1}, {}])
    git("reset", "-q", "--hard", "main", cwd=stack_repo)  # the chain is no longer the one you work on
    overview = _get(GerApi(stack_repo, gerrit=store), "/overview")
    [chain] = overview["sections"][1]["chains"]
    assert chain["local"] is False
    # Its failed CI waits until you look at it; the open comment is next in line.
    headline = overview["headline"]
    assert (headline["reason"], headline["number"]) == ("unresolved-comments", 101)


def test_a_review_chain_member_has_unresolved_comments_exactly_when_show_lists_an_open_chain(stack_repo: Path) -> None:
    # Review chains draws its comment icon from the member's reasons; the Change view lists /show's chains.
    # 100: a negative vote and no comment (A4). 101: an open comment. 102: only a resolved one.
    # The agreement rests on Gerrit deriving unresolved_comment_count from the published threads;
    # the fake sets the count and the comments independently, so each case sets both to agree.
    store, keys, rows = _gerrit(stack_repo, [{"cr": -1}, {"unresolved_comment_count": 1}, {}, {}])
    store.set_comments(
        keys[1], {"b.txt": [{"id": "c1", "line": 1, "message": "rename", "unresolved": True, "patch_set": 1}]}
    )
    store.set_comments(
        keys[2], {"c.txt": [{"id": "c2", "line": 1, "message": "typo", "unresolved": False, "patch_set": 1}]}
    )
    api = GerApi(stack_repo, gerrit=store)
    [chain] = _get(api, "/overview")["sections"][1]["chains"]
    flagged = {m["number"]: "unresolved-comments" in m["attention_reasons"] for m in chain["members"]}
    listed = {
        100 + i: any(
            not c["resolved"] for c in _get(api, "/show", rev=row.sha, comments="all")["commits"][0]["comment_chains"]
        )
        for i, row in enumerate(rows)
    }
    assert flagged == listed == {100: False, 101: True, 102: False, 103: False}
    assert "review-issues" in chain["members"][0]["attention_reasons"]


def test_headline_falls_back_to_the_longest_waiting_review(stack_repo: Path) -> None:
    store, keys, _rows = _gerrit(stack_repo)
    settings = Settings.from_cwd(stack_repo)
    waiting = dict(store.get_change(keys[0]), owner={"name": "alice", "_account_id": 2})
    store.add_change(waiting)  # the change is alice's on Gerrit, not only in the query's answer
    store.stub_query(build_to_review_query(settings, projects=["testproj"], include_unready=False), [waiting])
    store.stub_query(OUTGOING_QUERY, [])
    headline = _get(GerApi(stack_repo, gerrit=store), "/overview")["headline"]
    assert (headline["section"], headline["reason"], headline["number"]) == ("to-review", "waiting-on-you", 100)
    assert headline["text"].startswith("alice has waited ")
    assert headline["severity"] == "pending", "waiting on your review is no problem of the change"


def test_overview_shows_the_inbox_when_the_local_stack_cannot_be_read(stack_repo: Path) -> None:
    store, _keys, _rows = _gerrit(stack_repo)
    git("branch", "--unset-upstream", cwd=stack_repo)
    overview = _get(GerApi(stack_repo, gerrit=store), "/overview")
    assert overview["local"]["error"]["code"] == "GIT"
    assert [section["name"] for section in overview["sections"]] == ["to-review", "outgoing"]


def test_chain_is_the_review_chain_around_one_change(stack_repo: Path) -> None:
    store, _keys, _rows = _gerrit(stack_repo)
    chain = _get(GerApi(stack_repo, gerrit=store), "/chains/101")
    assert [m["number"] for m in chain["members"]] == [100, 101, 102, 103]
    assert any("change:100 OR change:101" in query for query in store.queries())


# --- Change detail -----------------------------------------------------------------


def test_show_carries_comment_chains_and_a_draft_reply_lands_in_its_chain(stack_repo: Path) -> None:
    store, keys, _rows = _gerrit(stack_repo, [{}, {}, {}, {"unresolved_comment_count": 1}])
    comment = {"id": "c1", "line": 1, "message": "rename this", "unresolved": True, "patch_set": 1}
    store.set_comments(keys[3], {"d.txt": [comment]})
    api = GerApi(stack_repo, gerrit=store)
    [commit] = _get(api, "/show", rev="HEAD", comments="all")["commits"]
    [chain] = commit["comment_chains"]
    assert (chain["root_id"], chain["path"], chain["line"], chain["resolved"]) == ("c1", "d.txt", 1, False)
    assert commit["short_sha"] and commit["local_commit"] is True

    answer = _post(api, "/comments/reply", {"rev": "HEAD", "comment_id": "c1", "message": "Done", "resolved": True})
    assert answer["root_id"] == "c1"
    assert (answer["reply"]["action"], answer["reply"]["unresolved"]) == ("created", False)
    [call] = store.calls_to("put_draft")
    assert call.args[2]["in_reply_to"] == "c1" and call.args[2]["message"] == "Done"
    # A draft, not a publish: nothing reaches other people from this endpoint.
    assert store.calls_to("publish_drafts") == []
    [chain] = _get(api, "/show", rev="HEAD", comments="all")["commits"][0]["comment_chains"]
    assert [c["state"] for c in chain["comments"]] == ["published", "draft"]
    assert chain["resolved_after_publish"] is True


def test_show_asks_gerrit_for_the_published_comments_and_your_drafts_at_once(stack_repo: Path) -> None:
    """The Change view's ``/show``: the two comment round trips run side by side, each once.

    Each call waits until the other has started; asked one after the other, the first one
    would wait in vain and the barrier breaks.
    """
    store, keys, _rows = _gerrit(stack_repo)
    store.set_comments(keys[3], {"d.txt": [{"id": "c1", "line": 1, "message": "rename", "unresolved": True}]})
    both = threading.Barrier(2, timeout=5)

    def meeting(fetch: Any) -> Any:
        def call(*args: Any, **kwargs: Any) -> Any:
            both.wait()
            return fetch(*args, **kwargs)

        return call

    store.get_comments = meeting(store.get_comments)  # type: ignore[method-assign]
    store.list_drafts = meeting(store.list_drafts)  # type: ignore[method-assign]
    [commit] = _get(GerApi(stack_repo, gerrit=store), "/show", rev="HEAD", comments="all", files="1")["commits"]
    assert [chain["root_id"] for chain in commit["comment_chains"]] == ["c1"]
    assert (len(store.calls_to("get_comments")), len(store.calls_to("list_drafts"))) == (1, 1)


def test_show_carries_suggested_edits_as_ger_show_json_does(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, keys, _rows = _gerrit(stack_repo, [{}, {}, {}, {"unresolved_comment_count": 2}])
    store.set_comments(keys[3], {"epsilon.txt": [FIX_COMMENT], "zeta.py": [BLOCK_COMMENT]})
    [commit] = _get(GerApi(stack_repo, gerrit=store), "/show", rev="HEAD", comments="all")["commits"]
    _code, out, err = run_cli(stack_repo, show_main, ["--json", "--comments", "all", "HEAD"], monkeypatch, gerrit=store)

    def edits(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            c["comment_id"]: c.get("suggested_edits") for chain in payload["comment_chains"] for c in chain["comments"]
        }

    served = edits(commit)
    # Where each replacement sits in the working files is the editor's (`worktree`, `original`), not `ger show`'s.
    placed = {"worktree", "original"}
    for found in served.values():
        for edit in found or []:
            edit["replacements"] = [{k: v for k, v in r.items() if k not in placed} for r in edit["replacements"]]
    assert served == edits(json_stdout(out)), err
    assert served["4f0a1c2b_8d9e7f60"][0]["fix_id"] == "a6b3c2d1_0e9f8a7b"
    assert served["7c6d5e4f_3a2b1c0d"][0]["in_message"] is True
    # And the text with the fix written out, both ways, so a viewer does not render it itself.
    texts = {
        c["comment_id"]: (c["text"], c["markdown"]) for chain in commit["comment_chains"] for c in chain["comments"]
    }
    assert texts["4f0a1c2b_8d9e7f60"] == (
        "Name the limit.\n\nsuggested fix: Use the LIMIT constant\n  epsilon.txt:2-3 becomes:\n"
        "  + LIMIT = 10\n  + use(LIMIT)",
        "Name the limit.\n\n**Suggested fix:** Use the LIMIT constant\n`epsilon.txt:2-3` becomes:\n"
        "```\nLIMIT = 10\nuse(LIMIT)\n```",
    )
    block = BLOCK_COMMENT["message"]
    assert texts["7c6d5e4f_3a2b1c0d"] == (block, block), "a suggestion block is in the text already"


def test_show_places_a_comment_of_a_stack_commit_in_the_working_file(stack_repo: Path) -> None:
    """A comment on an earlier commit, drawn in the working file: at its line there, or outdated."""
    store, keys, rows = _gerrit(stack_repo, [{}, {"unresolved_comment_count": 1}, {}, {}])
    block = "Say what it is.\n\n```suggestion\nfile b.txt, the router\n```"
    comment = {"id": "c1", "line": 1, "message": block, "unresolved": True, "patch_set": 1, "commit_id": rows[1].sha}
    store.set_comments(keys[1], {"b.txt": [comment]})
    api = GerApi(stack_repo, gerrit=store)

    def shown() -> tuple[Any, Any]:
        [chain] = _get(api, "/show", rev=rows[1].sha, comments="all")["commits"][0]["comment_chains"]
        [replacement] = chain["comments"][0]["suggested_edits"][0]["replacements"]
        return chain["worktree"], (replacement["worktree"], replacement["original"])

    (stack_repo / "b.txt").write_text("a line above\nfile b.txt\n", encoding="utf-8")
    moved = {"start_line": 2, "start_character": 0, "end_line": 3, "end_character": 0}
    assert shown() == ({"start_line": 2, "line": 2, "outdated": False}, (moved, "file b.txt\n"))
    (stack_repo / "b.txt").write_text("a line above\nfile b.txt, changed\n", encoding="utf-8")
    assert shown() == ({"start_line": 1, "line": 1, "outdated": True}, (None, "file b.txt\n"))


def test_show_places_a_comment_of_the_change_under_review_in_its_working_files(stack_repo: Path) -> None:
    """Staged for review here, a change's files are the working files: a comment on them is placed there too."""
    store, keys, rows = _gerrit(stack_repo)
    for number, row in enumerate(rows, start=100):
        git("update-ref", changes_ref(number, 1), row.sha, cwd=stack_repo)
    _post(GerApi(stack_repo, gerrit=store), "/review/open", {"change": 102})
    worktree = stack_repo.parent / f"{stack_repo.name}-review"
    [path] = [p for p in git_out("show", "--name-only", "--format=", rows[2].sha, cwd=stack_repo).split() if p]
    comment = {"id": "c1", "line": 1, "message": "x", "unresolved": True, "patch_set": 1, "commit_id": rows[2].sha}
    store.set_comments(keys[2], {path: [comment]})
    (worktree / path).write_text("a line above\n" + (worktree / path).read_text(encoding="utf-8"), encoding="utf-8")
    [chain] = _get(GerApi(worktree, gerrit=store), "/show", rev="change:102")["commits"][0]["comment_chains"]
    assert chain["worktree"] == {"start_line": 2, "line": 2, "outdated": False}


def test_show_claims_no_place_for_a_comment_on_a_version_that_is_not_here(stack_repo: Path) -> None:
    store, keys, rows = _gerrit(stack_repo, [{}, {"unresolved_comment_count": 1}, {}, {}])
    comment = {"id": "c1", "line": 1, "message": "x", "unresolved": True, "patch_set": 1, "commit_id": "f" * 40}
    store.set_comments(keys[1], {"b.txt": [comment]})
    [chain] = _get(GerApi(stack_repo, gerrit=store), "/show", rev=rows[1].sha)["commits"][0]["comment_chains"]
    assert chain["worktree"] is None


def test_show_marks_the_change_that_introduced_a_ci_finding(stack_repo: Path) -> None:
    # The same build fails on the two top changes: only the lower one introduced its findings.
    store, _keys, _rows = _gerrit(stack_repo, [{}, {}, {"verified": -1}, {"verified": -1}])
    api = GerApi(stack_repo, gerrit=store, ci_http=FakeCiHttp(jenkins_build()))
    commits = _get(api, "/show", stack="1", ci="1")["commits"]
    fingerprints = [f["fingerprint"] for f in commits[2]["ci_content"][0]["findings"]]
    assert fingerprints, "the Jenkins log has findings"
    assert [c["ci_introduced"] for c in commits] == [[], [], sorted(fingerprints), []]
    # One change on its own is not a chain: nothing claims to know what it introduced.
    assert "ci_introduced" not in _get(api, "/show", rev="HEAD", ci="1")["commits"][0]


def test_ci_of_one_change_asks_for_it_and_its_parent_and_agrees_with_the_whole_chain(stack_repo: Path) -> None:
    # What the Change view asks when its CI section opens (issue #40): every other member of the
    # chain would add its build logs and a round of comments to the wait.
    rows = stack_rows_mb_to_head(stack_repo)
    verified = [{}, {}, {"verified": -1}, {"verified": -1}]

    def ask(revs: list[str]) -> tuple[dict[str, Any], int, int]:
        store, _keys, _rows = _gerrit(stack_repo, verified)
        http = FakeCiHttp(jenkins_build())
        api = GerApi(stack_repo, gerrit=store, ci_http=http)
        commits = _get(api, "/show", rev=revs, ci="1", chain="1", refresh="1")["commits"]
        found = next(c for c in commits if c["sha"] == rows[2].sha)
        return found, len(store.calls), len(http.calls)

    def findings(commit: dict[str, Any]) -> tuple[list[Any], list[str]]:
        return [content["findings"] for content in commit["ci_content"]], commit["ci_introduced"]

    whole, whole_gerrit, whole_ci = ask([row.sha for row in rows])
    asked, gerrit_calls, ci_calls = ask([rows[1].sha, rows[2].sha])
    assert asked["ci_introduced"], "the change introduced its findings"
    assert findings(asked) == findings(whole)
    # Each count includes one ask for who you are: the changes list reviewers, so whether one
    # waits on your review could matter.
    assert (gerrit_calls, ci_calls) == (10, 2)
    # The two failing changes name the same build: the whole chain downloads it once too.
    assert (whole_gerrit, whole_ci) == (18, 2), "the whole chain: what the Change view asked before"


def test_show_serves_the_markdown_ger_show_ai_prints(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, keys, _rows = _gerrit(stack_repo, [{}, {}, {}, {"verified": -1, "unresolved_comment_count": 1}])
    comment = {"id": "c1", "line": 1, "message": "rename this", "unresolved": True, "patch_set": 1}
    store.set_comments(keys[3], {"d.txt": [comment]})
    api = GerApi(stack_repo, gerrit=store, ci_http=FakeCiHttp(jenkins_build()))
    [commit] = _get(api, "/show", rev="HEAD", comments="unresolved", context="2", markdown="1")["commits"]
    _code, out, err = run_cli(stack_repo, show_main, ["--ai", "--context", "HEAD"], monkeypatch, gerrit=store)
    # The command ends with a blank line between commits; the served Markdown ends with its last line.
    assert out == f"{commit['markdown']}\n", err
    assert "rename this" in commit["markdown"]
    [with_ci] = _get(api, "/show", rev="HEAD", ci="1", markdown="1")["commits"]
    assert "test_divide" in with_ci["markdown"]
    assert "markdown" not in _get(api, "/show", rev="HEAD")["commits"][0]


def test_agent_runbook_walks_the_commits_that_need_edits_and_keeps_holds_away(stack_repo: Path) -> None:
    # Two comments on the second commit; the same build fails on the two top commits.
    overrides = [{}, {"unresolved_comment_count": 2}, {"verified": -1}, {"verified": -1}]
    store, keys, rows = _gerrit(stack_repo, overrides)
    comments = [
        {"id": "c1", "line": 1, "message": "rename this", "unresolved": True, "patch_set": 1},
        {"id": "c2", "line": 1, "message": "is this the right design?", "unresolved": True, "patch_set": 1},
    ]
    store.set_comments(keys[1], {"b.txt": comments})
    api = GerApi(stack_repo, gerrit=store, ci_http=FakeCiHttp(jenkins_build()))

    # A design question is not for an agent: put it on hold.
    assert _post(api, "/comments/hold", {"root_id": "c2", "held": True})["holds"] == ["c2"]
    chains = _get(api, "/show", rev=rows[1].sha)["commits"][0]["comment_chains"]
    assert {chain["root_id"]: chain["held"] for chain in chains} == {"c1": False, "c2": True}

    runbook = _get(api, "/agent/runbook")
    by_sha = {commit["sha"]: commit for commit in runbook["commits"]}
    assert list(by_sha) == [rows[1].sha, rows[2].sha, rows[3].sha], "only commits that need edits, oldest first"
    assert (by_sha[rows[1].sha]["comments"], by_sha[rows[1].sha]["held"]) == (1, 1)
    # The findings are the lower commit's to fix; the upper one only inherits them.
    assert by_sha[rows[2].sha]["findings"] > 0 and by_sha[rows[2].sha]["inherited"] == 0
    assert (by_sha[rows[3].sha]["findings"], by_sha[rows[3].sha]["agent_work"]) == (0, False)
    assert by_sha[rows[3].sha]["inherited"] == by_sha[rows[2].sha]["findings"]
    assert runbook["totals"]["commits"] == 2 and runbook["totals"]["held"] == 1

    text = runbook["markdown"]
    # Commits are named by Change-Id: their ids change as the ones below are amended.
    assert f"`ger edit {rows[1].change_id}`" in text and f"`ger edit {rows[2].change_id}`" in text
    assert f"`ger edit {rows[3].change_id}`" not in text
    assert "`ger reply c1 --done`" in text and "ger reply c2" not in text
    assert "rename this" in text.split("### Left for a human")[0]
    assert "is this the right design?" in text.split("### Left for a human")[1]
    assert "its CI findings are fixed in an earlier commit" in text and "Never publish anything" in text

    # Narrowed to one commit, introduced findings are still judged against the whole stack.
    only = _get(api, "/agent/runbook", rev=rows[3].sha)
    assert [(c["sha"], c["findings"]) for c in only["commits"]] == [(rows[3].sha, 0)]
    assert _post(api, "/comments/hold", {"root_id": "c2", "held": False})["holds"] == []


def _content(*messages: str, error: str | None = None) -> CiContent:
    link = CiLink(label="jenkins", url="https://ci.example/1", source="message")
    findings = [CiFinding(tool="pytest", kind="failed", message=message) for message in messages]
    return CiContent(
        link=link, provenance=CiProvenance(url=link.url, provider="jenkins", error=error), findings=findings
    )


def test_introduced_findings_are_unknown_past_what_cannot_be_compared() -> None:
    a, b = (CiFinding(tool="pytest", kind="failed", message=m).fingerprint for m in ("a", "b"))
    assert introduced_fingerprints([[_content("a")], [_content("a", "b")], []]) == [{a}, {b}, frozenset()]
    # A commit Gerrit's CI did not see (None), or a parent whose log could not be fetched, hides the delta.
    assert introduced_fingerprints([None, [_content("a")]]) == [None, None]
    assert introduced_fingerprints([[_content(error="HTTP 404")], [_content("a")], [_content("a")]]) == [
        frozenset(),
        None,
        frozenset(),
    ]


def test_diff_lists_the_files_and_file_serves_both_sides(stack_repo: Path) -> None:
    store, _keys, rows = _gerrit(stack_repo)
    (stack_repo / "a.txt").write_text("file a.txt\nmore\n", encoding="utf-8")
    git("mv", "b.txt", "renamed.txt", cwd=stack_repo)
    git("commit", "-qam", "Touch and rename", cwd=stack_repo)
    api = GerApi(stack_repo, gerrit=store)
    diff = _get(api, "/diff", rev="HEAD")
    assert diff["base"] == rows[-1].sha and diff["local_commit"] is True
    by_path = {f["path"]: f for f in diff["files"]}
    assert by_path["a.txt"] == {"path": "a.txt", "status": "M", "old_path": None, "insertions": 1, "deletions": 0}
    assert (by_path["renamed.txt"]["status"], by_path["renamed.txt"]["old_path"]) == ("R", "b.txt")
    assert _get(api, "/file", rev=diff["commit"], path="a.txt")["content"] == "file a.txt\nmore\n"
    assert _get(api, "/file", rev=diff["base"], path="a.txt")["content"] == "file a.txt\n"
    assert _get(api, "/file", rev=diff["base"], path="renamed.txt")["content"] is None


def test_diff_fetches_a_change_that_is_not_in_the_local_stack(stack_repo: Path) -> None:
    store, _keys, rows = _gerrit(stack_repo)
    for number, row in enumerate(rows, start=100):
        git("update-ref", changes_ref(number, 1), row.sha, cwd=stack_repo)
    git("reset", "-q", "--hard", "main", cwd=stack_repo)  # reviewing someone else's work: no local stack
    diff = _get(GerApi(stack_repo, gerrit=store), "/diff", rev="change:102")
    assert (diff["commit"], diff["local_commit"]) == (rows[2].sha, False)
    assert [f["path"] for f in diff["files"]] == ["c.txt"]


def test_show_with_files_answers_what_diff_does_in_the_same_call(stack_repo: Path) -> None:
    store, _keys, _rows = _gerrit(stack_repo)
    git("mv", "b.txt", "renamed.txt", cwd=stack_repo)
    git("commit", "-qm", "Rename\n\nWhy it moved.", cwd=stack_repo)
    api = GerApi(stack_repo, gerrit=store)
    [shown] = _get(api, "/show", rev="HEAD", files="1")["commits"]
    assert shown["files"] == _get(api, "/diff", rev="HEAD")
    assert shown["files"]["message"] == "Rename\n\nWhy it moved."
    assert "files" not in _get(api, "/show", rev="HEAD")["commits"][0]


def test_show_with_files_fetches_a_change_that_is_not_in_the_local_stack(stack_repo: Path) -> None:
    store, _keys, rows = _gerrit(stack_repo)
    for number, row in enumerate(rows, start=100):
        git("update-ref", changes_ref(number, 1), row.sha, cwd=stack_repo)
    git("reset", "-q", "--hard", "main", cwd=stack_repo)  # reviewing someone else's work: no local stack
    [shown] = _get(GerApi(stack_repo, gerrit=store), "/show", rev="change:102", files="1")["commits"]
    assert (shown["files"]["commit"], shown["files"]["local_commit"]) == (rows[2].sha, False)
    assert [f["path"] for f in shown["files"]["files"]] == ["c.txt"]


def test_one_git_show_reads_a_merge_against_its_first_parent_and_a_root_against_nothing(stack_repo: Path) -> None:
    git("checkout", "-qb", "side", "main", cwd=stack_repo)
    (stack_repo / "side.txt").write_text("side\n", encoding="utf-8")
    git("add", "side.txt", cwd=stack_repo)
    git("commit", "-qm", "Side", cwd=stack_repo)
    git("checkout", "-q", "feature", cwd=stack_repo)
    git("merge", "-q", "--no-ff", "-m", "Merge side", "side", cwd=stack_repo)
    for rev in ("HEAD", git_out("rev-list", "--max-parents=0", "HEAD", cwd=stack_repo).split()[0]):
        shown = show_commit_diff(stack_repo, rev)
        assert (shown.diff, shown.meta) == (commit_diff(stack_repo, rev), read_commit_meta(stack_repo, rev))
    assert [f.path for f in show_commit_diff(stack_repo, "HEAD").diff.files] == ["side.txt"]
    with pytest.raises(UnknownRevisionError):
        show_commit_diff(stack_repo, "nosuchrev")


def test_diff_since_reviewed_is_the_current_patchset_against_the_one_you_voted_on(stack_repo: Path) -> None:
    store, keys, rows = _gerrit(stack_repo)
    # The author amends the top change after your review of patchset 1, and uploads patchset 2.
    (stack_repo / "d.txt").write_text("file d.txt\naddressed your comment\n", encoding="utf-8")
    git("commit", "-qa", "--amend", "--no-edit", cwd=stack_repo)
    amended = git_out("rev-parse", "HEAD", cwd=stack_repo)
    git("update-ref", changes_ref(103, 1), rows[3].sha, cwd=stack_repo)
    git("update-ref", changes_ref(103, 2), amended, cwd=stack_repo)
    store.upload_patchset(keys[3], amended, 2)
    mine = {"author": {"_account_id": 1000}, "_revision_number": 1, "message": "Patch Set 1: Code-Review-1"}
    theirs = {"author": {"_account_id": 2}, "_revision_number": 2, "message": "Uploaded patch set 2."}
    store.set_messages(keys[3], [mine, theirs])
    git("reset", "-q", "--hard", "main", cwd=stack_repo)
    api = GerApi(stack_repo, gerrit=store)

    delta = _get(api, "/diff", rev="change:103", since="reviewed")
    assert delta["since"] == {
        "patch_set": 1,
        "current_patch_set": 2,
        "to_patch_set": 2,
        "up_to_date": False,
        "identical": False,
        "rebased": False,
    }
    assert (delta["base"], delta["commit"]) == (rows[3].sha, amended)
    # d.txt is new in the change, but only modified since your review.
    assert delta["files"] == [{"path": "d.txt", "status": "M", "old_path": None, "insertions": 1, "deletions": 0}]
    assert _get(api, "/file", rev=delta["base"], path="d.txt")["content"] == "file d.txt\n"

    # Once you have posted on patchset 2 there is nothing new; a change you never touched says so.
    store.set_messages(keys[3], [mine, theirs, {**mine, "_revision_number": 2}])
    assert _get(api, "/diff", rev="change:103", since="reviewed")["since"]["up_to_date"] is True
    never = _get(api, "/diff", rev="change:102", since="reviewed")
    assert (never["since"]["patch_set"], never["files"], never["base"]) == (None, [], None)


def test_a_rebased_patchset_is_compared_only_in_the_files_the_change_touches(stack_repo: Path) -> None:
    rows = stack_rows_mb_to_head(stack_repo)
    old = rows[3].sha
    # Patchset 2: the same change, moved onto a parent that also edited another file.
    git("reset", "-q", "--hard", rows[2].sha, cwd=stack_repo)
    (stack_repo / "a.txt").write_text("upstream edit\n", encoding="utf-8")
    git("commit", "-qam", "Upstream edit", cwd=stack_repo)
    git("cherry-pick", old, cwd=stack_repo)
    (stack_repo / "d.txt").write_text("file d.txt\nfollow-up\n", encoding="utf-8")
    git("commit", "-qa", "--amend", "--no-edit", cwd=stack_repo)
    new = git_out("rev-parse", "HEAD", cwd=stack_repo)
    rebased, files = patchset_delta_files(stack_repo, old, new)
    assert rebased is True
    assert [f.path for f in files] == ["d.txt"], "a.txt changed underneath, not in the change"


@pytest.mark.parametrize("query", [{"rev": "--output=x"}, {}])
def test_revisions_from_the_client_never_reach_git_as_options(stack_repo: Path, query: dict[str, str]) -> None:
    store, _keys, _rows = _gerrit(stack_repo)
    with pytest.raises(BadRequest):
        _get(GerApi(stack_repo, gerrit=store), "/diff", **query)
    with pytest.raises(BadRequest):
        _get(GerApi(stack_repo, gerrit=store), "/file", rev="HEAD", path="a.txt")


# --- Actions -----------------------------------------------------------------------


def test_review_open_checks_out_in_the_review_worktree_and_done_leaves_it(stack_repo: Path) -> None:
    store, keys, rows = _gerrit(stack_repo)
    for number, row in enumerate(rows, start=100):
        git("update-ref", changes_ref(number, 1), row.sha, cwd=stack_repo)
    api = GerApi(stack_repo, gerrit=store)
    assert _get(api, "/review") == {"review": None}
    opened = _post(api, "/review/open", {"change": 102})["review"]
    worktree = stack_repo.parent / f"{stack_repo.name}-review"
    assert (opened["number"], opened["key"], opened["staged"]) == (102, keys[2], True)
    assert Path(opened["worktree"]) == worktree and opened["chain"] == [100, 101, 102, 103]
    assert git_out("diff", "--cached", "--name-only", cwd=worktree) == "c.txt"
    assert git_out("branch", "--show-current", cwd=stack_repo) == "feature", "your checkout is untouched"
    assert _get(api, "/review")["review"]["number"] == 102
    staged = _get(GerApi(worktree, gerrit=store), "/stack")["staged_review"]
    assert (staged["number"], staged["commit"], staged["branch"]) == (102, rows[2].sha, "change-102")
    assert _get(api, "/stack")["staged_review"] is None, "the review is staged in its worktree, not here"
    assert _get(api, "/review", chain="1")["review"]["chain"] == [100, 101, 102, 103]
    # Abandoned while under review, it keeps its place in the chain; another closed change does not.
    closed = {100: "MERGED", 102: "ABANDONED"}
    for key in keys:
        store.set_related(
            key,
            [
                {"_change_number": n, "_revision_number": 1, "status": closed.get(n, "NEW")}
                for n in (103, 102, 101, 100)
            ],
        )
    asked = _get(api, "/review", chain="1")["review"]
    assert asked["chain"] == [101, 102, 103]
    # Every member keeps its place with Gerrit's status: a client marks the closed ones in words.
    assert [(m["number"], m["status"]) for m in asked["members"]] == [
        (100, "MERGED"),
        (101, "NEW"),
        (102, "ABANDONED"),
        (103, "NEW"),
    ]
    assert _get(api, "/review")["review"]["members"] is None, "only asked for with chain=1"
    for key in keys:
        store.set_related(
            key, [{"_change_number": n, "_revision_number": 1, "status": "NEW"} for n in (103, 102, 101, 100)]
        )
    # ger review --next: the change above, in the review's own mode; at the top, the chain ends.
    walked = _post(api, "/review/open", {"step": 1})["review"]
    assert (walked["number"], walked["staged"], Path(walked["worktree"])) == (103, True, worktree)
    assert _get(GerApi(worktree, gerrit=store), "/stack")["staged_review"]["number"] == 103
    with pytest.raises(ReviewChainEndError, match="top of its review chain"):
        _post(api, "/review/open", {"step": 1})
    assert (
        failure_response(ReviewChainEndError("change 103 is the top of its review chain"))[1]["error"]["reason"]
        == "chain-end"
    )
    with pytest.raises(BadRequest):
        _post(api, "/review/open", {"step": 2})
    assert _post(api, "/review/open", {"step": -1})["review"]["number"] == 102
    done = _post(api, "/review/done", {})
    assert {k: done[k] for k in ("done", "deleted", "kept")} == {
        "done": 102,
        "deleted": ["change-102", "change-103"],
        "kept": [],
    }
    # Where the review's checkout is now: the review worktree, detached and free for the next review.
    assert (done["mode"], done["branch"], Path(done["worktree"])) == ("worktree", None, worktree)


def test_reorder_rewrites_the_stack_and_keeps_every_change_id(stack_repo: Path) -> None:
    store, _keys, rows = _gerrit(stack_repo)
    order = [rows[1].sha, rows[0].sha[:10], rows[3].sha, rows[2].sha]
    api = GerApi(stack_repo, gerrit=store)
    stack = _post(api, "/stack/rewrite", {"steps": order})
    after = stack_rows_mb_to_head(stack_repo)
    assert [c.subject for c in after] == [rows[i].subject for i in (1, 0, 3, 2)]
    assert [c.change_id for c in after] == [rows[i].change_id for i in (1, 0, 3, 2)]
    assert [c["sha"] for c in stack["commits"]] == [c.sha for c in after]
    assert not rebase_in_progress(stack_repo)
    # Only reordered: each commit's own code is the same, so its votes stay (Gerrit carries them over).
    moved = _get(api, "/stack")["commits"]
    assert {(c["patchset_status"], c["code_change"], c["code_review"], c["last_pushed_votes"]) for c in moved} == {
        ("newer", "rebased", 2, None)
    }
    # New code in the top commit: its votes are about the pushed patchset, served as that.
    (stack_repo / "more.txt").write_text("more code\n", encoding="utf-8")
    git("add", "-A", cwd=stack_repo)
    git("commit", "-q", "--amend", "--no-edit", cwd=stack_repo)
    top = _get(api, "/stack")["commits"][-1]
    assert (top["patchset_status"], top["verified"], top["code_review"]) == ("newer", None, None)
    assert top["last_pushed_votes"] == {"verified": 1, "code_review": 2}
    assert top["code_change"] == "changed", "the verdict the Local Stack row shows as code changed"


def test_a_commit_ahead_of_a_patchset_it_never_had_cannot_tell_whether_its_code_changed(stack_repo: Path) -> None:
    rows = stack_rows_mb_to_head(stack_repo)
    details = build_details_by_change_id(rows)
    web_edit(list(details.values())[1], rows[1].sha, keep_local_patchset=False)
    commits = _get(GerApi(stack_repo, gerrit=ChangeStore(details, web_base=WEB)), "/stack")["commits"]
    assert (commits[1]["patchset_status"], commits[1]["code_change"]) == ("newer", "unknown")
    assert commits[1]["last_pushed_votes"] is not None, "not proved only rebased: the votes are the pushed patchset's"


def test_an_empty_commit_ahead_of_its_patchset_cannot_tell_whether_its_code_changed(stack_repo: Path) -> None:
    """No diff, no patch-id: neither only rebased nor changed, and the votes are the pushed patchset's."""
    store, _keys, _rows = _gerrit(stack_repo)
    git_out("read-tree", "HEAD~1", cwd=stack_repo)
    git_out("commit", "--amend", "--allow-empty", "--no-edit", cwd=stack_repo)
    git_out("checkout", "--", ".", cwd=stack_repo)
    top = _get(GerApi(stack_repo, gerrit=store), "/stack")["commits"][-1]
    assert (top["patchset_status"], top["code_change"]) == ("newer", "unknown")
    assert top["last_pushed_votes"] is not None


def test_rewrite_folds_and_drops_without_ever_merging_two_change_ids(stack_repo: Path) -> None:
    store, _keys, rows = _gerrit(stack_repo)
    api = GerApi(stack_repo, gerrit=store)
    before = git_out("rev-parse", "HEAD", cwd=stack_repo)
    steps = [
        rows[0].sha,
        {"sha": rows[1].sha, "action": "fixup"},  # into rows[0], whose message stays
        {"sha": rows[2].sha, "action": "drop"},
        rows[3].sha,
    ]
    stack = _post(api, "/stack/rewrite", {"steps": steps})
    after = stack_rows_mb_to_head(stack_repo)
    assert [(c.subject, c.change_id) for c in after] == [(rows[i].subject, rows[i].change_id) for i in (0, 3)]
    assert git_out("show", "--format=", "--name-only", after[0].sha, cwd=stack_repo).split() == ["a.txt", "b.txt"]
    assert not (stack_repo / "c.txt").exists(), "the dropped commit's file is gone"
    assert stack["previous_head"] == before

    # Folding upwards-in-message: the folded commit's message, and so its Change-Id, wins.
    stack = _post(
        api, "/stack/rewrite", {"steps": [after[0].sha, {"sha": after[1].sha, "action": "fixup-take-message"}]}
    )
    [only] = stack_rows_mb_to_head(stack_repo)
    assert (only.subject, only.change_id) == (rows[3].subject, rows[3].change_id)
    assert only.body.count("Change-Id:") == 1

    # Undo takes exactly that rewrite back, and only while the branch is still where it left it.
    _post(api, "/stack/undo", {"head": stack["head"], "previous_head": stack["previous_head"]})
    assert [c.sha for c in stack_rows_mb_to_head(stack_repo)] == [c.sha for c in after]
    with pytest.raises(ReorderRefusal, match="has moved since the rewrite"):
        _post(api, "/stack/undo", {"head": stack["head"], "previous_head": before})


def test_rewrite_refuses_to_fold_the_lowest_commit(stack_repo: Path) -> None:
    store, _keys, rows = _gerrit(stack_repo)
    steps = [StackStep(rows[0].sha, "fixup"), *(StackStep(row.sha) for row in rows[1:])]
    with pytest.raises(ReorderRefusal, match="nothing below to fold it into"):
        rewrite_stack(stack_repo, steps, settings=Settings.from_cwd(stack_repo))
    with pytest.raises(BadRequest):
        _post(GerApi(stack_repo, gerrit=store), "/stack/rewrite", {"steps": [{"sha": rows[0].sha, "action": "squash"}]})


def test_a_reorder_that_conflicts_leaves_the_branch_as_it_was(stack_repo: Path) -> None:
    (stack_repo / "a.txt").write_text("rewritten\n", encoding="utf-8")
    git("commit", "-qam", "Rewrite a.txt\n\nChange-Id: I" + "5" * 40, cwd=stack_repo)
    settings = Settings.from_cwd(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    head = git_out("rev-parse", "HEAD", cwd=stack_repo)
    # The rewrite needs the commit that created a.txt below it.
    order = [rows[4].sha, *(row.sha for row in rows[:4])]
    reflog = git_out("reflog", cwd=stack_repo)
    with pytest.raises(ReorderRefusal, match=r"Rewrite a.txt.* does not apply in its new place \(conflict in a.txt\)"):
        rewrite_stack(stack_repo, [StackStep(sha) for sha in order], settings=settings)
    assert git_out("reflog", cwd=stack_repo) == reflog, "found out without starting a rebase"
    assert git_out("rev-parse", "HEAD", cwd=stack_repo) == head
    assert git_out("branch", "--show-current", cwd=stack_repo) == "feature"
    assert not rebase_in_progress(stack_repo)
    assert failure_response(ReorderRefusal("x"))[0] == 409


def test_a_conflict_the_preview_cannot_see_aborts_the_rebase(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(*_args: object) -> None:
        raise PreviewUnavailable("old git")

    monkeypatch.setattr(reorder, "first_conflict", unavailable)
    (stack_repo / "a.txt").write_text("rewritten\n", encoding="utf-8")
    git("commit", "-qam", "Rewrite a.txt\n\nChange-Id: I" + "5" * 40, cwd=stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    head = git_out("rev-parse", "HEAD", cwd=stack_repo)
    with pytest.raises(
        ReorderRefusal, match=r"Rewrite a\.txt.* does not apply in its new place \(conflict in a\.txt\);"
    ):
        rewrite_stack(
            stack_repo, [StackStep(r.sha) for r in [rows[4], *rows[:4]]], settings=Settings.from_cwd(stack_repo)
        )
    assert git_out("rev-parse", "HEAD", cwd=stack_repo) == head and not rebase_in_progress(stack_repo)


def test_a_rebase_git_refuses_says_why_instead_of_blaming_a_conflict(stack_repo: Path) -> None:
    hook = stack_repo / ".git" / "hooks" / "pre-rebase"
    hook.write_text("#!/bin/sh\necho 'error: rebasing is blocked by a hook' >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    rows = stack_rows_mb_to_head(stack_repo)
    head = git_out("rev-parse", "HEAD", cwd=stack_repo)
    with pytest.raises(ReorderRefusal, match=r"^git rebase failed: .*blocked by a hook; the stack was left as it was$"):
        rewrite_stack(
            stack_repo,
            [StackStep(r.sha) for r in [rows[1], rows[0], *rows[2:]]],
            settings=Settings.from_cwd(stack_repo),
        )
    assert git_out("rev-parse", "HEAD", cwd=stack_repo) == head and not rebase_in_progress(stack_repo)


def test_stack_moves_tells_which_neighbours_cannot_swap_without_touching_anything(stack_repo: Path) -> None:
    (stack_repo / "d.txt").write_text("reworked\n", encoding="utf-8")
    git("commit", "-qam", "Rework d.txt\n\nChange-Id: I" + "5" * 40, cwd=stack_repo)
    store, _keys, _rows = _gerrit(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    reflog = git_out("reflog", cwd=stack_repo)
    moves = _get(GerApi(stack_repo, gerrit=store), "/stack/moves")
    assert moves["available"] and moves["head"] == rows[4].sha
    assert [(swap["lower"], swap["upper"]) for swap in moves["swaps"]] == [
        (lower.sha, upper.sha) for lower, upper in pairwise(rows)
    ]
    # Only the commit that reworks d.txt cannot go below the one that adds it.
    assert [swap["conflict"] for swap in moves["swaps"][:3]] == [None, None, None]
    assert moves["swaps"][3]["conflict"] == {"sha": rows[4].sha, "summary": "Rework d.txt", "files": ["d.txt"]}
    assert git_out("reflog", cwd=stack_repo) == reflog and not git_out("status", "--porcelain", cwd=stack_repo)


def test_stack_moves_follows_a_swap_whose_result_differs_through_the_rest_of_the_stack(stack_repo: Path) -> None:
    rows = stack_rows_mb_to_head(stack_repo)
    base = git_out("rev-parse", "main", cwd=stack_repo)
    # Dropping the commit that adds c.txt is clean; moving the top one to the bottom too.
    assert first_conflict(stack_repo, base, [rows[0], rows[1], rows[3]], rows) is None
    assert first_conflict(stack_repo, base, [rows[3], rows[0], rows[1], rows[2]], rows) is None
    assert swap_conflicts(stack_repo, base, rows) == [None, None, None]
    with pytest.raises(PreviewUnavailable):
        first_conflict(stack_repo, "0" * 40, [rows[1]], rows)


def _linear(repo: Path, contents: list[str]) -> tuple[str, list[Commit]]:
    """A repository whose ``f.txt`` takes each of *contents* in turn: the first is the base."""
    git("init", "-q", cwd=repo)
    for key, value in (("user.email", "t@t"), ("user.name", "t"), ("core.autocrlf", "false")):
        git("config", key, value, cwd=repo)
    commits = []
    for index, text in enumerate(contents):
        (repo / "f.txt").write_text(text, encoding="utf-8")
        git("add", "f.txt", cwd=repo)
        git("commit", "-qm", f"step {index}", cwd=repo)
        sha = git_out("rev-parse", "HEAD", cwd=repo)
        commits.append(Commit(sha=sha, short_sha=sha[:7], subject=f"step {index}", body="", change_id=None))
    return commits[0].sha, commits[1:]


def test_a_swap_that_changes_the_content_is_followed_up_the_stack(tmp_path: Path) -> None:
    # step 2 reverts step 1: swapped, the pair leaves "y" where "x" was, and step 3 no longer applies.
    base, stack = _linear(tmp_path, ["x\n", "y\n", "x\n", "z\n", "z\nmore\n"])
    swaps = swap_conflicts(tmp_path, base, stack)
    assert swaps[0] == PickConflict(stack[2], ("f.txt",))
    assert swaps == [
        first_conflict(tmp_path, base, [*stack[:i], stack[i + 1], stack[i], *stack[i + 2 :]], stack)
        for i in range(len(stack) - 1)
    ]


def test_a_git_that_cannot_batch_the_preview_gets_the_same_answer(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (stack_repo / "d.txt").write_text("reworked\n", encoding="utf-8")
    git("commit", "-qam", "Rework d.txt\n\nChange-Id: I" + "5" * 40, cwd=stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    base = git_out("rev-parse", "main", cwd=stack_repo)
    batched = swap_conflicts(stack_repo, base, rows)
    monkeypatch.setattr(reorder_preview, "_picks_at_once", lambda *_args: None)
    assert swap_conflicts(stack_repo, base, rows) == batched
    assert batched[3] == PickConflict(rows[4], ("d.txt",))


def test_reorder_refuses_a_stale_order_and_a_dirty_worktree(stack_repo: Path) -> None:
    settings = Settings.from_cwd(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    with pytest.raises(ReorderRefusal, match="each of the 4 local stack commits once"):
        rewrite_stack(stack_repo, [StackStep(row.sha) for row in rows[:3]], settings=settings)
    (stack_repo / "a.txt").write_text("edited\n", encoding="utf-8")
    with pytest.raises(ReorderRefusal, match="uncommitted changes"):
        rewrite_stack(stack_repo, [StackStep(row.sha) for row in reversed(rows)], settings=settings)


def test_reorder_says_a_commit_is_gone_or_an_id_too_short_not_both_at_once(stack_repo: Path) -> None:
    settings = Settings.from_cwd(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    gone = "f" * 40
    with pytest.raises(ReorderRefusal, match=r"^'ffffffffffff' is not a commit of the local stack \(.*reload it\)$"):
        rewrite_stack(stack_repo, [StackStep(sha) for sha in [gone, *(row.sha for row in rows[1:])]], settings=settings)
    stack = [dataclasses.replace(rows[0], sha=sha) for sha in ("ab" + "1" * 38, "ab" + "2" * 38)]
    with pytest.raises(ReorderRefusal, match="'ab' names 2 commits of the local stack"):
        reorder._resolve_steps(stack, [StackStep("ab")])  # pylint: disable=protected-access


def test_reorder_off_the_branch_says_so_instead_of_missing_the_commits_it_was_shown(stack_repo: Path) -> None:
    # The view shows the branch; off it, HEAD..upstream no longer holds those commits.
    settings = Settings.from_cwd(stack_repo)
    order = [row.sha for row in reversed(stack_rows_mb_to_head(stack_repo))]
    git("checkout", "-q", "--detach", "HEAD~2", cwd=stack_repo)
    with pytest.raises(ReorderRefusal, match="HEAD is detached"):
        rewrite_stack(stack_repo, [StackStep(sha) for sha in order], settings=settings)
    git("checkout", "-q", "feature", cwd=stack_repo)
    git(
        "rebase",
        "-q",
        "-i",
        "HEAD~2",
        cwd=stack_repo,
        env={"GIT_SEQUENCE_EDITOR": "sed -i 1s/^pick/edit/"},
        check=False,
    )
    assert rebase_in_progress(stack_repo)
    with pytest.raises(ReorderRefusal, match="a rebase is already in progress"):
        rewrite_stack(stack_repo, [StackStep(sha) for sha in order], settings=settings)


def test_stack_names_the_commit_each_fixup_folds_into(stack_repo: Path) -> None:
    store, _keys, rows = _gerrit(stack_repo)
    git("commit", "-q", "--allow-empty", "-m", f"fixup! {rows[1].subject}", cwd=stack_repo)
    git("commit", "-q", "--allow-empty", "-m", "fixup! no such commit", cwd=stack_repo)
    commits = _get(GerApi(stack_repo, gerrit=store), "/stack")["commits"]
    assert [c["fixup_of"] for c in commits] == [None, None, None, None, rows[1].sha, None]


def test_rebase_refusal_is_the_check_every_go_on_makes_unstaged_changes_too(stack_repo: Path) -> None:
    api = GerApi(stack_repo, gerrit=_gerrit(stack_repo)[0])
    assert _get(api, "/rebase/refusal")["refused"].startswith("no rebase is in progress, nothing to continue")
    stop = {"GIT_SEQUENCE_EDITOR": "sed -i 1s/^pick/edit/"}
    git("rebase", "-q", "-i", "HEAD~2", cwd=stack_repo, env=stop, check=False)
    assert _get(api, "/rebase/refusal", action="continue") == {"action": "continue", "refused": None}
    (stack_repo / "a.txt").write_text("edited at the stop\n", encoding="utf-8")
    refused = _get(api, "/rebase/refusal")["refused"]
    assert refused.startswith("you have unstaged changes to a.txt; amend them in (`git commit --amend -a`)")
    # The stack says it too, with the files, before anybody presses Continue: one verdict, one wording.
    rebase = _get(api, "/stack")["rebase"]
    assert (rebase["uncommitted"], rebase["actions"]) == (["a.txt"], ["abort"])
    assert rebase["refused"]["continue"] == refused
    with pytest.raises(BadRequest, match="action must be one of continue, abort, skip"):
        _get(api, "/rebase/refusal", action="onward")


def test_a_stop_serves_what_continue_amends_in_apart_from_what_it_refuses_over(stack_repo: Path) -> None:
    """Staged changes go into the commit on Continue without a word: listed, they are said before."""
    api = GerApi(stack_repo, gerrit=_gerrit(stack_repo)[0])
    git(
        "rebase",
        "-q",
        "-i",
        "HEAD~2",
        cwd=stack_repo,
        env={"GIT_SEQUENCE_EDITOR": "sed -i 1s/^pick/edit/"},
        check=False,
    )
    tracked = sorted(git_out("ls-files", cwd=stack_repo).split())[:2]
    for name in tracked:
        (stack_repo / name).write_text(f"{name} edited at the stop\n", encoding="utf-8")
    git("add", tracked[0], cwd=stack_repo)
    rebase = _get(api, "/stack")["rebase"]
    assert (rebase["staged"], rebase["uncommitted"]) == ([tracked[0]], [tracked[1]])
    git("add", tracked[1], cwd=stack_repo)
    rebase = _get(api, "/stack")["rebase"]
    assert (rebase["staged"], rebase["uncommitted"], rebase["actions"]) == (tracked, [], ["continue", "abort"])
    (stack_repo / tracked[0]).write_text("staged, then changed again\n", encoding="utf-8")
    assert (_get(api, "/stack")["rebase"]["staged"], _get(api, "/stack")["rebase"]["uncommitted"]) == (
        tracked,
        [tracked[0]],
    )


# --- Transport ---------------------------------------------------------------------


@pytest.fixture
def served(stack_repo: Path) -> Iterator[GerHttpServer]:
    store, _keys, _rows = _gerrit(stack_repo)
    server = GerHttpServer(GerApi(stack_repo, gerrit=store), token="s3cret")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def _http(
    server: GerHttpServer,
    path: str,
    *,
    token: str | None = "s3cret",
    host: str | None = None,
    data: bytes | None = None,
) -> tuple[int, dict[str, Any]]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    if host:
        headers["Host"] = host
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(server.url + path, headers=headers, data=data)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_http_answers_only_with_the_token_on_a_loopback_host(served: GerHttpServer) -> None:
    status, body = _http(served, f"{V1}/health")
    assert status == 200
    assert (body["name"], body["api"], body["branch"], body["host"]) == ("ger", "1.10.0", "feature", "g.example")
    assert _http(served, f"{V1}/health", token=None)[0] == 401
    assert _http(served, f"{V1}/health", token="wrong")[0] == 401
    # A page that rebinds its own name to 127.0.0.1 still sends its name as Host.
    assert _http(served, f"{V1}/health", host="evil.example")[0] == 403


def test_debug_log_times_each_request_with_its_git_and_gerrit_calls(
    served: GerHttpServer, caplog: pytest.LogCaptureFixture
) -> None:
    """``--debug-log``: one line per request, how long it ran and queued, and the calls it made."""
    log = logging.getLogger("gerrit_workflow_tools.server.httpd")
    log.addHandler(caplog.handler)
    level = log.level
    log.setLevel(logging.DEBUG)
    call_trace.set_call_trace_enabled(True)
    try:
        assert _http(served, f"{V1}/health")[0] == 200
    finally:
        call_trace.set_call_trace_enabled(False)
        log.setLevel(level)
        log.removeHandler(caplog.handler)
    # The handler may see each record twice: as added here, and through the root logger.
    [timing] = {record.getMessage() for record in caplog.records if " ms queued" in record.getMessage()}
    assert re.match(rf"GET {V1}/health: \d+ ms, after \d+ ms queued\nrequest trace", timing), timing
    assert "  git " in timing
    assert call_trace.format_call_trace_summary() == "", "each request's calls are its own"


def test_a_request_naming_its_action_answers_with_its_trace(served: GerHttpServer) -> None:
    """``X-Ger-Action`` in, ``X-Ger-Trace`` out: run and queue time, and the git and Gerrit calls it made."""

    def get(headers: dict[str, str]) -> Any:
        request = urllib.request.Request(
            served.url + f"{V1}/stack", headers={"Authorization": "Bearer s3cret", **headers}
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()
            return response.headers.get("X-Ger-Trace")

    assert get({}) is None, "no trace unless asked"
    trace = json.loads(get({"X-Ger-Action": "a7"}))
    assert trace["action"] == "a7"
    assert trace["ms"] >= 0 and trace["queued_ms"] >= 0
    assert trace["git"]["count"] > 0 and trace["git"]["count"] == sum(1 for call in trace["calls"] if call[0] == "git")
    assert set(trace) == {"action", "ms", "queued_ms", "git", "gerrit", "calls", "calls_left_out"}


def test_http_maps_failures_to_statuses_and_shared_codes(served: GerHttpServer) -> None:
    assert _http(served, f"{V1}/nope") == (
        404,
        {"error": {"code": "NOT_FOUND", "message": f"no such endpoint: GET {V1}/nope"}},
    )
    status, body = _http(served, f"{V1}/show?rev=nosuchrev")
    assert (status, body["error"]["code"]) == (404, "NOT_FOUND")
    status, body = _http(served, f"{V1}/stack/rewrite", data=b'{"steps": "abc"}')
    assert (status, body["error"]["code"]) == (400, "USAGE")
    status, body = _http(served, f"{V1}/stack/rewrite", data=b"not json")
    assert (status, body["error"]["code"]) == (400, "USAGE")
    assert failure_response(NoRoute("x"))[0] == 404
    assert failure_response(RuntimeError("bug"))[1]["error"]["code"] == "INTERNAL"


def test_http_skips_a_read_its_caller_called_off_while_it_queued(
    served: GerHttpServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Change view abandons the load of a commit you walked past: ger serve does not run it."""
    handled: list[str] = []
    answer = served.api.handle
    asked = threading.Event()
    left = _Handler._caller_left

    def handle(method: str, path: str, *rest: Any) -> dict[str, Any]:
        handled.append(path)
        return answer(method, path, *rest)

    def caller_left(self: _Handler) -> bool:
        asked.set()
        return left(self)

    monkeypatch.setattr(served.api, "handle", handle)
    monkeypatch.setattr(_Handler, "_caller_left", caller_left)
    port = int(served.url.rsplit(":", 1)[1])
    # The lock is held, as by a request under way, until the caller has hung up.
    with served.api_lock, socket.create_connection(("127.0.0.1", port)) as caller:
        caller.sendall(f"GET {V1}/show HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer s3cret\r\n\r\n".encode())
    assert asked.wait(timeout=10)
    assert _http(served, f"{V1}/health")[0] == 200  # a caller that waits is answered
    assert f"{V1}/show" not in handled


def test_http_says_nothing_but_a_debug_line_when_the_caller_hung_up_before_its_answer(
    served: GerHttpServer, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A client that went away (the Change view moved on) is no error: no traceback, one debug line."""
    hung_up, asked, answered = threading.Event(), threading.Event(), threading.Event()

    def handle(_method: str, _path: str, *_rest: Any) -> dict[str, Any]:
        asked.set()
        assert hung_up.wait(timeout=10)
        return {"big": "x" * 8_000_000}  # more than a socket buffer: the write meets the closed connection

    monkeypatch.setattr(served.api, "handle", handle)
    log = logging.getLogger("gerrit_workflow_tools.server.httpd")
    seen: list[str] = []

    class Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen.append(record.getMessage())
            if "went away" in record.getMessage():
                answered.set()

    collect, level = Collect(), log.level
    log.addHandler(collect)
    log.setLevel(logging.DEBUG)
    try:
        port = int(served.url.rsplit(":", 1)[1])
        caller = socket.create_connection(("127.0.0.1", port))
        caller.sendall(f"GET {V1}/show HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer s3cret\r\n\r\n".encode())
        assert asked.wait(timeout=10)
        caller.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        caller.close()  # a reset, as an aborted fetch leaves it
        hung_up.set()
        assert answered.wait(timeout=10), seen
    finally:
        log.setLevel(level)
        log.removeHandler(collect)
    assert "Traceback" not in capsys.readouterr().err
    assert _http(served, f"{V1}/health")[0] == 200  # it serves on


def test_serve_prints_where_it_listens_and_stops_with_stdin(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, _keys, _rows = _gerrit(stack_repo)
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"")))
    code, out, err = run_cli(stack_repo, serve_main, ["--exit-with-stdin"], monkeypatch, gerrit=store)
    assert code == ExitCode.OK, err
    hello = json.loads(out.splitlines()[0])
    assert hello["url"].startswith("http://127.0.0.1:") and hello["api"] == V1
    assert len(hello["token"]) >= 32 and Path(hello["repo"]) == stack_repo


def test_serve_outside_a_git_checkout_says_why_in_its_one_json_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    code, out, _err = run_cli(tmp_path, serve_main, ["--exit-with-stdin"], monkeypatch)
    assert code == ExitCode.GIT
    error = json.loads(out.splitlines()[0])["error"]
    assert error == {"code": "GIT", "message": "not a git repository; run ger inside a clone"}


def test_a_checkout_without_gerrit_settings_answers_config(tmp_path: Path) -> None:
    git("init", "-q", cwd=tmp_path)
    with pytest.raises(Exception) as raised:
        GerApi(tmp_path).handle("GET", f"{V1}/overview")
    status, body = failure_response(raised.value)
    assert (status, body["error"]["code"], body["error"]["reason"]) == (500, "CONFIG", "no-web-url")
    assert "ger.webUrl" in body["error"]["message"]


def test_a_plain_http_web_url_is_refused_with_its_reason(tmp_path: Path) -> None:
    """The setting at fault, as a word a client words its advice from; ger's own words in the message."""
    git("init", "-q", cwd=tmp_path)
    git("config", "ger.webUrl", "http://gerrit.example:8081", cwd=tmp_path)
    with pytest.raises(Exception) as raised:
        GerApi(tmp_path).handle("GET", f"{V1}/overview")
    status, body = failure_response(raised.value)
    assert (status, body["error"]["code"], body["error"]["reason"]) == (500, "CONFIG", "insecure-http")
    assert "git config ger.allowInsecureHttp true" in body["error"]["message"]


# --- Upstream and new comments -----------------------------------------------------


def test_a_branch_without_upstream_says_so_and_gets_one_suggested(stack_repo: Path) -> None:
    store, _keys, _rows = _gerrit(stack_repo)
    api = GerApi(stack_repo, gerrit=store)
    git("branch", "--unset-upstream", cwd=stack_repo)
    git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main", cwd=stack_repo)
    local = _get(api, "/overview")["local"]
    assert "error" in local and local["needs_upstream"] is True
    choice = _get(api, "/stack/upstream")
    # The remote's HEAD is no branch to track (git abbreviates it to plain `origin`).
    assert not {"origin", "origin/HEAD"} & set(choice["candidates"])
    assert (choice["branch"], choice["upstream"]) == ("feature", None)
    # The template fetched its own branches: origin/feature is where feature is.
    assert (choice["suggested"], choice["why"]) == ("origin/feature", "nearest: 0 ahead, 0 behind")
    assert choice["candidates"][0] == "origin/feature" and "origin/main" in choice["candidates"]

    assert _post(api, "/stack/upstream", {"upstream": "origin/main"}) == {
        "branch": "feature",
        "upstream": "origin/main",
    }
    local = _get(api, "/overview")["local"]
    assert "error" not in local and len(local["commits"]) == 4
    assert _get(api, "/stack/upstream")["upstream"] == "origin/main"
    with pytest.raises(UnknownRevisionError, match="not a remote-tracking branch"):
        _post(api, "/stack/upstream", {"upstream": "origin/nowhere"})


def test_the_branch_a_branch_was_created_from_beats_the_nearest(stack_repo: Path) -> None:
    api = GerApi(stack_repo, gerrit=_gerrit(stack_repo)[0])
    git("checkout", "-q", "--no-track", "-b", "topic", "origin/main", cwd=stack_repo)
    git("reset", "-q", "--hard", "feature", cwd=stack_repo)
    choice = _get(api, "/stack/upstream")
    assert (choice["branch"], choice["upstream"]) == ("topic", None)
    assert (choice["suggested"], choice["why"]) == ("origin/main", "the branch was created from it")


def test_new_comments_go_to_gerrit_as_drafts_or_stay_here_as_notes(stack_repo: Path) -> None:
    store, _keys, _rows = _gerrit(stack_repo)
    api = GerApi(stack_repo, gerrit=store)
    draft = _post(api, "/comments/new", {"rev": "HEAD", "location": "d.txt:1", "message": "Why?"})
    assert _get(api, "/show", rev="HEAD")["commits"][0]["new_comment"] == "draft"
    assert (draft["state"], draft["path"], draft["line"], draft["unresolved"]) == ("draft", "d.txt", 1, True)
    [call] = store.calls_to("put_draft")
    assert call.args[2]["message"] == "Why?" and call.args[2]["line"] == 1

    note = _post(api, "/comments/new", {"rev": "HEAD", "location": "d.txt", "message": "mine", "state": "note"})
    assert (note["state"], note["patch_set"], note["url"]) == ("note", None, None)
    assert len(store.calls_to("put_draft")) == 1, "a note never reaches Gerrit"
    [commit] = _get(api, "/show", rev="HEAD", comments="all")["commits"]
    states = sorted(c["state"] for chain in commit["comment_chains"] for c in chain["comments"])
    assert states == ["draft", "note"]


def test_a_new_comment_on_the_change_or_its_commit_message_is_served_as_on_the_command_line(stack_repo: Path) -> None:
    """``ger comment``'s Gerrit paths, for a change-level draft and one on the commit message."""
    store, _keys, _rows = _gerrit(stack_repo)
    api = GerApi(stack_repo, gerrit=store)
    change = _post(api, "/comments/new", {"rev": "HEAD", "location": "/PATCHSET_LEVEL", "message": "Overall fine."})
    assert (change["state"], change["path"], change["line"]) == ("draft", "/PATCHSET_LEVEL", None)
    message = _post(api, "/comments/new", {"rev": "HEAD", "location": "/COMMIT_MSG:7", "message": "typo"})
    assert (message["path"], message["line"]) == ("/COMMIT_MSG", 7)
    [commit] = _get(api, "/show", rev="HEAD", comments="all")["commits"]
    assert sorted(chain["path"] for chain in commit["comment_chains"]) == ["/COMMIT_MSG", "/PATCHSET_LEVEL"]


def test_a_new_comment_goes_on_the_version_of_the_change_that_was_shown(stack_repo: Path) -> None:
    store, keys, rows = _gerrit(stack_repo)
    old = rows[3].sha
    # The change is reworked here: Gerrit still has patchset 1, the commit HEAD names is new.
    (stack_repo / "d.txt").write_text("reworked\nwith a second line\n", encoding="utf-8")
    git("commit", "-qa", "--amend", "--no-edit", cwd=stack_repo)
    api = GerApi(stack_repo, gerrit=store)
    assert _get(api, "/show", rev="HEAD")["commits"][0]["new_comment"] == "local-draft"

    # A viewer showing patchset 1 says so: the draft is on patchset 1, its location checked against its lines.
    shown = {"rev": "HEAD", "commit": old, "message": "Why?"}
    draft = _post(api, "/comments/new", {**shown, "location": "d.txt:1"})
    assert (draft["state"], draft["patch_set"], draft["commit"]) == ("draft", 1, old)
    [call] = store.calls_to("put_draft")
    assert call.args[:2] == (keys[3], 1)
    with pytest.raises(BadRequest, match=r"past the end|line"):
        _post(api, "/comments/new", {**shown, "location": "d.txt:2"})  # a line only the rework has
    # A note on it is anchored to the patchset's lines too.
    note = _post(api, "/comments/new", {**shown, "location": "d.txt:1", "state": "note"})
    assert (note["state"], note["commit"]) == ("note", old)

    # The commit rev names is fine to name; another change's commit, or anything that is not a commit id, is not.
    head = git_out("rev-parse", "HEAD", cwd=stack_repo)
    own = _post(api, "/comments/new", {"rev": "HEAD", "commit": head, "location": "d.txt:2", "message": "x"})
    assert (own["state"], own["commit"]) == ("local-draft", head)
    with pytest.raises(ChangeResolutionError, match="is not a patchset of"):
        _post(api, "/comments/new", {**shown, "commit": rows[0].sha, "location": "a.txt:1"})
    with pytest.raises(BadRequest, match="commit must be a commit id"):
        _post(api, "/comments/new", {**shown, "commit": "HEAD~1", "location": "d.txt:1"})
    assert len(store.calls_to("put_draft")) == 1


def test_a_commit_gerrit_does_not_have_gets_a_local_draft(stack_repo: Path) -> None:
    store, _keys, _rows = _gerrit(stack_repo)
    (stack_repo / "d.txt").write_text("reworked\n", encoding="utf-8")
    git("commit", "-qam", "Rework d.txt\n\nChange-Id: I" + "5" * 40, cwd=stack_repo)
    api = GerApi(stack_repo, gerrit=store)
    assert _get(api, "/show", rev="HEAD")["commits"][0]["new_comment"] == "local-draft"
    local = _post(api, "/comments/new", {"rev": "HEAD", "location": "d.txt:1", "message": "later", "resolved": True})
    assert (local["state"], local["unresolved"], local["patch_set"]) == ("local-draft", False, None)
    assert store.calls_to("put_draft") == []
    with pytest.raises(BadRequest, match=r"past the end|line"):
        _post(api, "/comments/new", {"rev": "HEAD", "location": "d.txt:99", "message": "x"})
    with pytest.raises(BadRequest, match="state must be one of"):
        _post(api, "/comments/new", {"rev": "HEAD", "location": "d.txt:1", "message": "x", "state": "published"})


def test_your_unpublished_comments_can_be_edited_and_discarded(stack_repo: Path) -> None:
    store, _keys, _rows = _gerrit(stack_repo)
    api = GerApi(stack_repo, gerrit=store)
    draft = _post(api, "/comments/new", {"rev": "HEAD", "location": "d.txt:1", "message": "Why?"})
    note = _post(api, "/comments/new", {"rev": "HEAD", "location": "d.txt:1", "message": "mine", "state": "note"})

    def yours() -> dict[str, str]:
        [commit] = _get(api, "/show", rev="HEAD", comments="all")["commits"]
        return {c["state"]: c["message"] for chain in commit["comment_chains"] for c in chain["comments"]}

    edited = _post(api, "/comments/edit", {"rev": "HEAD", "comment_id": draft["id"], "message": " Why so? "})
    assert (edited["action"], edited["state"], edited["message"]) == ("updated", "draft", "Why?")
    call = store.calls_to("put_draft")[-1]
    # The draft itself is updated, where it is and as open as it was.
    assert call.kwargs["draft_id"] == draft["id"]
    assert (call.args[2]["line"], call.args[2]["unresolved"], call.args[2]["message"]) == (1, True, "Why so?")
    body = {"rev": "HEAD", "comment_id": note["id"], "message": "ours", "resolved": True}
    edited = _post(api, "/comments/edit", body)
    assert (edited["state"], edited["message"]) == ("note", "mine")
    assert yours() == {"draft": "Why so?", "note": "ours"}
    assert len(store.calls_to("put_draft")) == 2, "a note never reaches Gerrit"

    with pytest.raises(BadRequest, match="message is empty"):
        _post(api, "/comments/edit", {"rev": "HEAD", "comment_id": note["id"], "message": "  "})
    with pytest.raises(ChangeResolutionError, match="no comment nope of yours"):
        _post(api, "/comments/edit", {"rev": "HEAD", "comment_id": "nope", "message": "x"})

    gone = _post(api, "/comments/discard", {"rev": "HEAD", "comment_id": draft["id"]})
    assert (gone["action"], gone["state"], gone["message"]) == ("discarded", "draft", "Why so?")
    assert _post(api, "/comments/discard", {"rev": "HEAD", "comment_id": note["id"]})["message"] == "ours"
    assert yours() == {}
    assert store.calls_to("publish_drafts") == []
    with pytest.raises(ChangeResolutionError):
        _post(api, "/comments/discard", {"rev": "HEAD", "comment_id": note["id"]})


def test_stack_says_which_commits_push_gives_a_change_id_and_holds_none_back(stack_repo: Path) -> None:
    """State 19 of the outside-the-happy-path run: the rows said "held back", the push sent them as new changes."""
    store = _gerrit(stack_repo)[0]
    git("commit", "--allow-empty", "-q", "-m", "no change id", cwd=stack_repo)
    git("commit", "--allow-empty", "-q", "-m", "no change id either", cwd=stack_repo)
    git("config", "ger.stopPattern", "^nothing-matches$", cwd=stack_repo)
    stack = _get(GerApi(stack_repo, gerrit=store), "/stack")
    assert [(c["held_back"], c["adds_change_id"]) for c in stack["commits"]][-2:] == [(False, True), (False, True)]
