# Spec: docu/spec/commands/serve.md § GET /vote, POST /vote; docu/spec/commands/review.md § Voting and publishing
"""``ger serve``'s vote: the plan a reviewer confirms, and a publish that sends what ``ger review --vote`` sends."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_reply import main as reply_main
from gerrit_workflow_tools.cli_review import main as review_main
from gerrit_workflow_tools.core.git_run import git
from gerrit_workflow_tools.core.usage import Refusal, UsageError
from gerrit_workflow_tools.server.api import GerApi
from gerrit_workflow_tools.server.httpd import failure_response
from tests.change_store import ChangeStore
from tests.conftest import json_stdout, run_cli
from tests.test_serve import V1, WEB, _gerrit


BOB = {"name": "Bob", "email": "bob@example.com", "_account_id": 2000}
MEANINGS = {
    "-2": "This shall not be submitted",
    "-1": "I would prefer this is not submitted as is",
    " 0": "No score",
    "+1": "Looks good to me, but someone else must approve",
    "+2": "Looks good to me, approved",
}


def _bobs(repo: Path) -> tuple[ChangeStore, list[str]]:
    """The stack as Bob's chain 100-103 on Gerrit; you may give Code-Review -1 to +1 on each, -2 to +2 on 103."""
    store, keys, _rows = _gerrit(repo)
    for key in keys:
        row = store._changes[key]  # pylint: disable=protected-access
        row["owner"] = dict(BOB)
        row["labels"]["Code-Review"]["values"] = dict(MEANINGS)
        row["permitted_labels"] = {"Code-Review": ["-1", " 0", "+1"], "Verified": [" 0"]}
    store._changes[keys[3]]["permitted_labels"]["Code-Review"] = list(MEANINGS)  # pylint: disable=protected-access
    return store, keys


def _draft(store: ChangeStore, key: str, message: str, line: int = 1) -> None:
    store.set_drafts(key, {"c.txt": [{"id": f"d-{message}", "line": line, "message": message, "patch_set": 1}]})


def _get(api: GerApi, **query: str | list[str]) -> dict[str, Any]:
    return api.handle("GET", f"{V1}/vote", {k: v if isinstance(v, list) else [v] for k, v in query.items()})


def _post(api: GerApi, body: dict[str, Any]) -> dict[str, Any]:
    return api.handle("POST", f"{V1}/vote", {}, body)


def test_the_plan_lists_the_drafts_that_go_out_and_the_votes_on_offer(stack_repo: Path) -> None:
    store, keys = _bobs(stack_repo)
    _draft(store, keys[2], "nit: name this\nand more", line=3)
    plan = _get(GerApi(stack_repo, gerrit=store), change="102")
    [change] = plan["changes"]
    assert (change["number"], change["subject"], change["patch_set"], change["own"]) == (
        102,
        store.get_change(keys[2])["subject"],
        1,
        False,
    )
    assert change["owner"] == "Bob" and change["url"] == f"{WEB}/c/testproj/+/102"
    assert change["drafts"] == [
        {
            "path": "c.txt",
            "line": 3,
            "location": "c.txt:3",
            "text": "nit: name this",
            "transition": None,
            "suggested_edit": False,
        }
    ]
    assert plan["drafts"] == 1 and store.calls_to("publish_drafts") == []
    offered = [(o["label"], o["value"], o["meaning"], o["takes"]) for o in plan["options"]]
    assert offered == [
        ("Code-Review", 1, MEANINGS["+1"], [102]),
        ("Code-Review", 0, "No score", [102]),
        ("Code-Review", -1, MEANINGS["-1"], [102]),
        ("Verified", 0, "", [102]),
    ]


def test_a_chain_plan_offers_every_vote_some_change_takes_and_says_which(stack_repo: Path) -> None:
    store, keys = _bobs(stack_repo)
    _draft(store, keys[0], "base nit")
    plan = _get(GerApi(stack_repo, gerrit=store), change="101", chain="1", vote="+2")
    assert [c["number"] for c in plan["changes"]] == [100, 101, 102, 103]
    assert [len(c["drafts"]) for c in plan["changes"]] == [1, 0, 0, 0]
    takes = {(o["label"], o["value"]): o["takes"] for o in plan["options"]}
    assert takes[("Code-Review", 2)] == [103] and takes[("Code-Review", 1)] == [100, 101, 102, 103]
    assert [bool(c["ruled_out"]) for c in plan["changes"]] == [True, True, True, False]
    assert plan["changes"][0]["ruled_out"] == ["Code-Review+2 (you may give -1, 0, +1)"]


def test_your_own_change_is_marked_yours(stack_repo: Path) -> None:
    store, _keys, _rows = _gerrit(stack_repo)  # owned by account 1000: you
    assert _get(GerApi(stack_repo, gerrit=store), change="102")["changes"][0]["own"] is True


def test_the_served_vote_sends_what_ger_review_vote_sends(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One plan, one send: the same Gerrit requests, and the same answer, from the command and from the service."""
    git("config", "ger.webUrl", WEB, cwd=stack_repo)
    served, keys = _bobs(stack_repo)
    _draft(served, keys[1], "nit")
    answer = _post(
        GerApi(stack_repo, gerrit=served),
        {
            "change": 101,
            "chain": True,
            "votes": {"Code-Review": 1},
            "message": "lgtm",
            "patch_sets": {str(n): 1 for n in range(100, 104)},
        },
    )
    by_cli, _keys = _bobs(stack_repo)
    _draft(by_cli, keys[1], "nit")
    named = [arg for n in range(100, 104) for arg in ("--change", str(n))]
    code, out, err = run_cli(
        stack_repo, review_main, ["--vote", "+1", *named, "-m", "lgtm", "-y", "--json"], monkeypatch, gerrit=by_cli
    )
    assert code == ExitCode.OK, err
    calls = [(c.args, c.kwargs) for c in by_cli.calls_to("publish_drafts")]
    assert [(c.args, c.kwargs) for c in served.calls_to("publish_drafts")] == calls
    assert [args[0] for args, _kwargs in calls] == keys
    assert {str(kwargs) for _args, kwargs in calls} == {
        str({"message": "lgtm", "labels": {"Code-Review": 1}, "draft_ids": None})
    }
    assert answer == json_stdout(out)
    assert answer["changes"][1] == {
        "change": keys[1],
        "number": 101,
        "drafts": 1,
        "labels": {"Code-Review": 1},
        "keeps": {},
        "message": "lgtm",
        "status": "published",
    }
    assert served.list_drafts(keys[1]) == {}


def test_a_vote_on_changes_that_moved_since_the_plan_sends_nothing(stack_repo: Path) -> None:
    store, keys = _bobs(stack_repo)
    api = GerApi(stack_repo, gerrit=store)
    store.upload_patchset(keys[2], "f" * 40, 2)
    with pytest.raises(Refusal, match="change 102 is at patchset 2 now, not 1: look again") as moved:
        _post(api, {"change": 102, "votes": {"Code-Review": 1}, "patch_sets": {"102": 1}})
    with pytest.raises(Refusal, match="the changes are 100, 101, 102, 103 now, not 102 as shown"):
        _post(api, {"change": 102, "chain": True, "votes": {"Code-Review": 1}, "patch_sets": {"102": 2}})
    assert failure_response(moved.value)[0] == 409
    assert store.calls_to("publish_drafts") == []


def test_drafts_go_out_without_a_vote_as_ger_reply_publish_sends_them(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no vote the served publish is ``ger reply --publish CHANGEISH``; with nothing at all it is refused."""
    git("config", "ger.webUrl", WEB, cwd=stack_repo)
    store, keys = _bobs(stack_repo)
    api = GerApi(stack_repo, gerrit=store)
    with pytest.raises(UsageError, match="nothing to publish"):
        _post(api, {"change": 102, "votes": {}, "patch_sets": {"102": 1}})
    _draft(store, keys[2], "nit")
    answer = _post(api, {"change": 102, "votes": {}, "patch_sets": {"102": 1}})
    assert answer["changes"][0]["drafts"] == 1 and answer["changes"][0]["labels"] == {}
    by_cli, _keys = _bobs(stack_repo)
    _draft(by_cli, keys[2], "nit")
    code, _out, err = run_cli(stack_repo, reply_main, ["--publish", "102", "-y"], monkeypatch, gerrit=by_cli)
    assert code == ExitCode.OK, err
    sent = [(c.args, c.kwargs) for c in store.calls_to("publish_drafts")]
    assert sent == [(c.args, c.kwargs) for c in by_cli.calls_to("publish_drafts")]
    assert sent == [((keys[2],), {"message": None, "labels": None, "draft_ids": None})]


def test_a_vote_the_change_does_not_take_and_the_publish_guard_are_refused(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _keys = _bobs(stack_repo)
    api = GerApi(stack_repo, gerrit=store)
    with pytest.raises(UsageError, match=r"change 102 does not take Code-Review\+2 \(you may give -1, 0, \+1\)"):
        _post(api, {"change": 102, "votes": {"Code-Review": 2}, "patch_sets": {"102": 1}})
    monkeypatch.setenv("GER_NO_PUBLISH", "1")
    with pytest.raises(UsageError, match="publishing is blocked: GER_NO_PUBLISH"):
        _post(api, {"change": 102, "votes": {"Code-Review": 1}, "patch_sets": {"102": 1}})
    assert store.calls_to("publish_drafts") == []


def test_show_says_someone_elses_change_is_published_by_a_vote(stack_repo: Path) -> None:
    store, _keys = _bobs(stack_repo)
    bobs = _show(GerApi(stack_repo, gerrit=store), "change:102")
    assert (bobs["own"], bobs["drafts_published_by"]) == (False, "vote")


def test_show_says_your_own_commit_is_published_by_your_push(stack_repo: Path) -> None:
    mine, _keys, _rows = _gerrit(stack_repo)
    local = _show(GerApi(stack_repo, gerrit=mine), "HEAD")
    assert (local["own"], local["drafts_published_by"]) == (True, "push")


def _show(api: GerApi, rev: str) -> dict[str, Any]:
    [commit] = api.handle("GET", f"{V1}/show", {"rev": [rev]})["commits"]
    return commit


def test_a_chain_vote_keeps_your_vote_and_says_so_before_it_is_sent(stack_repo: Path) -> None:
    """The editor's chain-level Vote and Publish: the plan says which changes keep your vote, and the
    publish leaves them alone (finding 2 of the 2026-11-25 review walk)."""
    git("config", "ger.webUrl", WEB, cwd=stack_repo)
    store, keys = _bobs(stack_repo)
    api = GerApi(stack_repo, gerrit=store)
    _post(api, {"change": 101, "votes": {"Code-Review": -1}, "patch_sets": {"101": 1}})
    plan = _get(api, change="101", chain="1")
    assert [change["yours"] for change in plan["changes"]] == [{}, {"Code-Review": -1}, {}, {}]
    [plus_one] = [o for o in plan["options"] if (o["label"], o["value"]) == ("Code-Review", 1)]
    assert plus_one["keeps"] == [101]
    assert _get(api, change="101", chain="1", vote="+1")["changes"][1]["keeps"] == {"Code-Review": -1}
    del store.calls[:]
    sent = _post(
        api,
        {"change": 101, "chain": True, "votes": {"Code-Review": 1}, "patch_sets": {str(n): 1 for n in range(100, 104)}},
    )
    assert [row["number"] for row in sent["changes"]] == [100, 102, 103]
    assert [call.args[0] for call in store.calls_to("publish_drafts")] == [keys[0], keys[2], keys[3]]
