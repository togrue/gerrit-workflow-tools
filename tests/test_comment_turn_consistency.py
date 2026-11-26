"""Whose turn a comment chain is: ``ger log``, ``ger show``, ``ger inbox``, the attention walk and
``ger serve`` say the same.

A chain whose last published comment is yours waits on the other side: the commit reads as
awaiting a reply (``pending``), the walk passes it, and nothing counts it as needing you. One you
answered in a draft waits on your push: drafts to publish (``pending``), passed by the walk too. Same pattern as
``tests/test_attention_consistency.py``: agreement between commands, not per command.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gerrit_workflow_tools.cli_inbox import main as inbox_main
from gerrit_workflow_tools.cli_log import main as log_main
from gerrit_workflow_tools.cli_show import main as show_main
from gerrit_workflow_tools.core.stack import Commit
from gerrit_workflow_tools.server.api import GerApi
from tests.change_store import ChangeStore
from tests.conftest import json_stdout, run_cli
from tests.test_serve import V1, _gerrit


ME, ALICE = 1000, 2000


def _comment(cid: str, author: int, updated: str, *, in_reply_to: str | None = None) -> dict[str, Any]:
    comment: dict[str, Any] = {
        "id": cid,
        "line": 3,
        "message": cid,
        "unresolved": True,
        "updated": updated,
        "patch_set": 1,
        "author": {"_account_id": author, "name": "me" if author == ME else "Alice"},
    }
    return comment | ({"in_reply_to": in_reply_to} if in_reply_to else {})


@pytest.fixture
def turns(stack_repo: Path) -> tuple[Path, ChangeStore, list[Commit]]:
    """Four changes: the bottom one's comment you answered, the second's you have not, the third's
    you answered in a draft; the top has none."""
    commented = {"unresolved_comment_count": 1}
    store, keys, rows = _gerrit(stack_repo, [commented, commented, commented, {"total_comment_count": 0}])
    store.set_comments(
        keys[0],
        {
            "a.txt": [
                _comment("asked", ALICE, "2026-11-09 10:00:00"),
                _comment("answered", ME, "2026-11-09 11:00:00", in_reply_to="asked"),
            ]
        },
    )
    store.set_comments(keys[1], {"a.txt": [_comment("open", ALICE, "2026-11-09 10:00:00")]})
    store.set_comments(keys[2], {"a.txt": [_comment("drafted", ALICE, "2026-11-09 10:00:00")]})
    store.set_drafts(keys[2], {"a.txt": [_comment("mine", ME, "2026-11-09 12:00:00", in_reply_to="drafted")]})
    return stack_repo, store, rows


def _hint(line: str) -> str:
    return line.rsplit("  # ", 1)[1].strip() if "  # " in line else ""


def test_log_show_walk_and_serve_agree_on_whose_turn(
    turns: tuple[Path, ChangeStore, list[Commit]], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, store, rows = turns
    code, out, err = run_cli(repo, log_main, ["--color", "never"], monkeypatch, gerrit=store)
    assert code == 0, err
    in_log = {line.split()[0]: _hint(line) for line in out.splitlines() if " # " in line}
    assert "3 commits with comments" not in out
    assert "1 commit with comments · 1 commit with drafts to publish · 1 awaiting reply" in out
    assert in_log[rows[0].short_sha].startswith("1 comment waits on the reviewer")
    assert in_log[rows[1].short_sha].startswith("1 unresolved comment")
    assert in_log[rows[2].short_sha].startswith("1 draft to publish")
    _code, out, _err = run_cli(repo, log_main, ["--json"], monkeypatch, gerrit=store)
    logged = json_stdout(out)["commits"]
    log_reasons = [c["attention_reasons"] for c in logged]
    assert "awaiting-reply" in log_reasons[0] and "unresolved-comments" not in log_reasons[0]
    assert "unresolved-comments" in log_reasons[1]
    assert "drafts-to-publish" in log_reasons[2] and "unresolved-comments" not in log_reasons[2]
    assert [c["comments_awaiting_reply"] for c in logged] == [1, 0, 0, 0]
    assert [c["comments_drafted"] for c in logged] == [0, 0, 1, 0]
    assert [c["comments_to_answer"] for c in logged] == [0, 1, 0, 0]
    assert [c["comments_unresolved"] for c in logged] == [1, 1, 1, 0], "the count stays a fact"
    # Every chain counts towards the share, resolved or not; Gerrit's zero needs no reading.
    assert [c["comments_total"] for c in logged] == [1, 1, 1, 0]

    turn_of = {}
    for back, row in enumerate(reversed(rows)):
        _code, shown, _err = run_cli(repo, show_main, [f"HEAD~{back}", "--color", "never"], monkeypatch, gerrit=store)
        assert _hint(shown.splitlines()[0]) == in_log[row.short_sha], row.short_sha
        _code, shown, _err = run_cli(repo, show_main, [f"HEAD~{back}", "--json"], monkeypatch, gerrit=store)
        payload = json_stdout(shown)
        assert payload["attention_reasons"] == log_reasons[len(rows) - 1 - back]
        turn_of[row.short_sha] = [chain["turn"] for chain in payload["comment_chains"]]
    assert [turn_of[row.short_sha] for row in rows] == [["theirs"], ["yours"], ["draft"], []]
    _code, shown, _err = run_cli(repo, show_main, ["HEAD~3", "--color", "never"], monkeypatch, gerrit=store)
    assert "a.txt:3 (their turn)" in shown
    _code, shown, _err = run_cli(repo, show_main, ["HEAD~1", "--color", "never"], monkeypatch, gerrit=store)
    assert "a.txt:3 (draft to publish)" in shown

    # The walk starts at the first commit whose turn it is, not at the one waiting on the reviewer.
    _code, out, err = run_cli(repo, show_main, ["--first-attention", "--json"], monkeypatch, gerrit=store)
    assert json_stdout(out)["sha"] == rows[1].sha, err

    api = GerApi(repo, gerrit=store)
    stack = api.handle("GET", f"{V1}/stack", {})
    assert [c["attention_reasons"] for c in stack["commits"]] == log_reasons
    assert [c["needs_edit"] for c in stack["commits"]] == [False, True, False, False]
    assert [c["comments_to_answer"] for c in stack["commits"]] == [0, 1, 0, 0]
    assert stack["commits"][0]["severity"] == stack["commits"][2]["severity"] == "pending"


def test_the_inbox_counts_a_chain_waiting_on_the_reviewer_as_not_needing_you(
    turns: tuple[Path, ChangeStore, list[Commit]], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, store, _rows = turns
    overview = GerApi(repo, gerrit=store).handle("GET", f"{V1}/overview", {})
    [served] = overview["sections"][1]["chains"]
    code, out, err = run_cli(repo, inbox_main, ["--json"], monkeypatch, gerrit=store)
    assert code == 0, err
    [listed] = next(s for s in json_stdout(out)["sections"] if s["name"] == "outgoing")["chains"]
    members = [m["attention_reasons"] for m in served["members"]]
    assert members == [m["attention_reasons"] for m in listed["members"]]
    assert members[0] == ["awaiting-reply"]
    assert members[2] == ["drafts-to-publish"]
    assert [m["comments_awaiting_reply"] for m in served["members"]] == [1, 0, 0, 0]
    assert [m["comments_drafted"] for m in served["members"]] == [0, 0, 1, 0]
    assert (served["comments_to_answer"], served["comments_drafted"]) == (1, 1)
    assert [m["comments_total"] for m in served["members"]] == [m["comments_total"] for m in listed["members"]]
    assert [m["comments_total"] for m in served["members"]] == [1, 1, 1, 0]
    # The headline is the first comment that is yours to answer, not the one waiting on the reviewer.
    assert (overview["headline"]["reason"], overview["headline"]["number"]) == ("unresolved-comments", 101)
    code, out, _err = run_cli(repo, inbox_main, ["--color", "never"], monkeypatch, gerrit=store)
    assert f"c{100}  v+1 cr+2 com  # 1 comment waits on the reviewer" in out
    assert f"c{102}  v+1 cr+2 com  # 1 draft to publish" in out


def test_the_walk_passes_a_commit_whose_comments_your_drafts_answer_and_says_to_push(stack_repo: Path) -> None:
    from gerrit_workflow_tools.cli_edit import resolve_first_edit_attention_sha
    from gerrit_workflow_tools.core.attention import NoEditAttentionError
    from gerrit_workflow_tools.core.config import Settings
    from gerrit_workflow_tools.core.run_context import RunContext

    store, keys, _rows = _gerrit(stack_repo, [{"unresolved_comment_count": 1}, {}, {}, {}])
    store.set_comments(keys[0], {"a.txt": [_comment("asked", ALICE, "2026-11-09 10:00:00")]})
    store.set_drafts(keys[0], {"a.txt": [_comment("done", ME, "2026-11-09 12:00:00", in_reply_to="asked")]})
    run = RunContext.open(stack_repo, settings=Settings.from_cwd(stack_repo), gerrit=store)
    with pytest.raises(NoEditAttentionError) as ended:
        resolve_first_edit_attention_sha(run)
    assert str(ended.value).splitlines()[1:] == ["1 commit has drafts to publish: ger push"]


def test_a_commit_held_back_only_by_comments_your_drafts_answer_waits_on_your_push(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not "blocked by 3 unresolved comments": you answered them; the push publishes the answers."""
    store, keys, rows = _gerrit(stack_repo, [{"unresolved_comment_count": 1, "submittable": False}, {}, {}, {}])
    store.set_comments(keys[0], {"a.txt": [_comment("asked", ALICE, "2026-11-09 10:00:00")]})
    store.set_drafts(keys[0], {"a.txt": [_comment("done", ME, "2026-11-09 12:00:00", in_reply_to="asked")]})
    code, out, err = run_cli(stack_repo, log_main, ["--color", "never"], monkeypatch, gerrit=store)
    assert code == 0, err
    assert f"chain waits on your push: {rows[0].short_sha} has 1 draft to publish" in out
    assert "chain blocked by" not in out


def test_the_walk_ends_naming_the_comments_it_passed_over(stack_repo: Path) -> None:
    """A comment waiting on the reviewer is no stop of the walk; its end says so."""
    from gerrit_workflow_tools.cli_edit import resolve_first_edit_attention_sha
    from gerrit_workflow_tools.core.attention import NoEditAttentionError
    from gerrit_workflow_tools.core.config import Settings
    from gerrit_workflow_tools.core.run_context import RunContext

    store, keys, rows = _gerrit(stack_repo, [{"unresolved_comment_count": 1}, {}, {}, {}])
    store.set_comments(
        keys[0],
        {
            "a.txt": [
                _comment("asked", ALICE, "2026-11-09 10:00"),
                _comment("re", ME, "2026-11-09 11:00", in_reply_to="asked"),
            ]
        },
    )
    run = RunContext.open(stack_repo, settings=Settings.from_cwd(stack_repo), gerrit=store)
    with pytest.raises(NoEditAttentionError) as ended:
        resolve_first_edit_attention_sha(run)
    assert str(ended.value).splitlines() == [
        "no commit needs edit attention (comments to answer, a failed build or a negative vote)",
        f"1 commit has comments waiting on a reply: ger show {rows[0].short_sha}",
    ]


def test_whose_turn_costs_one_comments_read_per_commented_change_and_nothing_once_cached(
    turns: tuple[Path, ChangeStore, list[Commit]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0013: who you are is remembered, comments are cached on the change's freshness. Your
    drafts are never cached: one ``has:draft`` query among the commented changes, then the drafts
    of those it names, every run."""
    repo, store, _rows = turns
    run_cli(repo, log_main, [], monkeypatch, gerrit=store)
    cold = [call.method for call in store.calls]
    assert cold.count("get_comments") == 3 and cold.count("get_account") == 1, cold
    drafted = [call for call in store.calls if call.method == "query_changes" and "has:draft" in call.args[0]]
    assert len(drafted) == 1 and cold.count("list_drafts") == 1, cold
    store.calls.clear()
    run_cli(repo, log_main, [], monkeypatch, gerrit=store)
    warm = [call.method for call in store.calls]
    assert "get_comments" not in warm and "get_account" not in warm, warm
    assert warm.count("list_drafts") == 1, warm


def test_a_mix_of_turns_counts_what_is_yours_to_answer() -> None:
    from gerrit_workflow_tools.render.status_fmt import AUTHOR, unresolved_comments_text

    assert unresolved_comments_text(3) == "3 unresolved comments"
    assert unresolved_comments_text(3, 1) == "2 of 3 comments to answer"
    assert unresolved_comments_text(2, 2, side=AUTHOR) == "2 comments wait on the author"
    assert unresolved_comments_text(3, 0, 3) == "3 drafts to publish"
    assert unresolved_comments_text(3, 0, 1) == "2 of 3 comments to answer, 1 draft to publish"
    assert unresolved_comments_text(3, 2, 1) == "1 draft to publish, 2 comments wait on the reviewer"
