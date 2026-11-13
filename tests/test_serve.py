# Spec: docu/spec/commands/serve.md
"""``ger serve``: the API through ``GerApi.handle``, the transport through a real socket."""

from __future__ import annotations

import dataclasses
import io
import json
import socket
import sys
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest

from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_serve import main as serve_main
from gerrit_workflow_tools.cli_show import main as show_main
from gerrit_workflow_tools.core import reorder, reorder_preview
from gerrit_workflow_tools.core.ci_content import CiContent, CiFinding, CiProvenance, introduced_fingerprints
from gerrit_workflow_tools.core.ci_links import CiLink
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit_patchset import changes_ref
from gerrit_workflow_tools.core.git_run import UnknownRevisionError, git, git_out
from gerrit_workflow_tools.core.inbox import DEFAULT_OUTGOING_QUERY, build_to_review_query
from gerrit_workflow_tools.core.rebase_state import rebase_in_progress
from gerrit_workflow_tools.core.reorder import ReorderRefusal, StackStep, reorder_stack, rewrite_stack
from gerrit_workflow_tools.core.reorder_preview import PickConflict, PreviewUnavailable, first_conflict, swap_conflicts
from gerrit_workflow_tools.core.review_delta import patchset_delta_files
from gerrit_workflow_tools.core.stack import Commit
from gerrit_workflow_tools.server.api import GerApi
from gerrit_workflow_tools.server.httpd import GerHttpServer, _Handler, failure_response
from gerrit_workflow_tools.server.request import BadRequest, NoRoute
from tests.change_store import ChangeStore
from tests.ci_fakes import JENKINS, FakeCiHttp, jenkins_build
from tests.cli_gerrit_mocks import build_details_by_change_id, stack_rows_mb_to_head
from tests.conftest import json_stdout, run_cli


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
    return api.handle("GET", f"{V1}{endpoint}", params)


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
    assert [c["last_pushed_votes"] for c in stack["commits"]] == [None] * 4, "in step with Gerrit: votes are current"


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
    # Failed CI outranks unresolved comments, wherever each sits in the chain.
    headline = overview["headline"]
    assert (headline["section"], headline["reason"], headline["number"]) == ("outgoing", "ci-failed", 102)
    assert headline["text"] == "CI failed on 102: test! temporary experiment"
    assert overview["local"]["summary"]["commits"] == 4


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
    assert _post(api, "/review/done", {}) == {"done": 102, "deleted": ["change-102"], "kept": []}


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
    assert {(c["patchset_status"], c["code_review"], c["last_pushed_votes"]) for c in moved} == {("newer", 2, None)}
    # New code in the top commit: its votes are about the pushed patchset, served as that.
    (stack_repo / "more.txt").write_text("more code\n", encoding="utf-8")
    git("add", "-A", cwd=stack_repo)
    git("commit", "-q", "--amend", "--no-edit", cwd=stack_repo)
    top = _get(api, "/stack")["commits"][-1]
    assert (top["patchset_status"], top["verified"], top["code_review"]) == ("newer", None, None)
    assert top["last_pushed_votes"] == {"verified": 1, "code_review": 2}


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
        reorder_stack(stack_repo, order, settings=settings)
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
        reorder_stack(stack_repo, [rows[4].sha, *(r.sha for r in rows[:4])], settings=Settings.from_cwd(stack_repo))
    assert git_out("rev-parse", "HEAD", cwd=stack_repo) == head and not rebase_in_progress(stack_repo)


def test_a_rebase_git_refuses_says_why_instead_of_blaming_a_conflict(stack_repo: Path) -> None:
    hook = stack_repo / ".git" / "hooks" / "pre-rebase"
    hook.write_text("#!/bin/sh\necho 'error: rebasing is blocked by a hook' >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    rows = stack_rows_mb_to_head(stack_repo)
    head = git_out("rev-parse", "HEAD", cwd=stack_repo)
    with pytest.raises(ReorderRefusal, match=r"^git rebase failed: .*blocked by a hook; the stack was left as it was$"):
        reorder_stack(
            stack_repo, [rows[1].sha, rows[0].sha, *(r.sha for r in rows[2:])], settings=Settings.from_cwd(stack_repo)
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
        reorder_stack(stack_repo, [row.sha for row in rows[:3]], settings=settings)
    (stack_repo / "a.txt").write_text("edited\n", encoding="utf-8")
    with pytest.raises(ReorderRefusal, match="uncommitted changes"):
        reorder_stack(stack_repo, [row.sha for row in reversed(rows)], settings=settings)


def test_reorder_says_a_commit_is_gone_or_an_id_too_short_not_both_at_once(stack_repo: Path) -> None:
    settings = Settings.from_cwd(stack_repo)
    rows = stack_rows_mb_to_head(stack_repo)
    gone = "f" * 40
    with pytest.raises(ReorderRefusal, match=r"^'ffffffffffff' is not a commit of the local stack \(.*reload it\)$"):
        reorder_stack(stack_repo, [gone, *(row.sha for row in rows[1:])], settings=settings)
    stack = [dataclasses.replace(rows[0], sha=sha) for sha in ("ab" + "1" * 38, "ab" + "2" * 38)]
    with pytest.raises(ReorderRefusal, match="'ab' names 2 commits of the local stack"):
        reorder._resolve_steps(stack, [StackStep("ab")])  # pylint: disable=protected-access


def test_reorder_off_the_branch_says_so_instead_of_missing_the_commits_it_was_shown(stack_repo: Path) -> None:
    # The view shows the branch; off it, HEAD..upstream no longer holds those commits.
    settings = Settings.from_cwd(stack_repo)
    order = [row.sha for row in reversed(stack_rows_mb_to_head(stack_repo))]
    git("checkout", "-q", "--detach", "HEAD~2", cwd=stack_repo)
    with pytest.raises(ReorderRefusal, match="HEAD is detached"):
        reorder_stack(stack_repo, order, settings=settings)
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
        reorder_stack(stack_repo, order, settings=settings)


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
    assert (body["name"], body["api"], body["branch"], body["host"]) == ("ger", 1, "feature", "g.example")
    assert _http(served, f"{V1}/health", token=None)[0] == 401
    assert _http(served, f"{V1}/health", token="wrong")[0] == 401
    # A page that rebinds its own name to 127.0.0.1 still sends its name as Host.
    assert _http(served, f"{V1}/health", host="evil.example")[0] == 403


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
    assert (status, body["error"]["code"]) == (500, "CONFIG")
    assert "gerrit.webUrl" in body["error"]["message"]


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
