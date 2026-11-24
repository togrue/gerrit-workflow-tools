# Spec: docu/spec/commands/reply.md

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_reply import main as reply_main
from gerrit_workflow_tools.cli_show import main as show_main
from gerrit_workflow_tools.core.gerrit.rest import GerritApiError
from tests.change_store import ChangeStore
from tests.cli_gerrit_mocks import build_details_by_change_id, stack_rows_mb_to_head
from tests.conftest import WEB_STACK_REPO_URL, json_stdout, run_cli


WEB = WEB_STACK_REPO_URL


def _comment(cid: str, message: str, *, unresolved: bool, updated: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": cid,
        "line": 3,
        "message": message,
        "unresolved": unresolved,
        "updated": updated,
        "patch_set": 1,
        **extra,
    }


def _store_with_open_chain(repo: Path, **root_extra: Any) -> tuple[ChangeStore, str]:
    """Stack on Gerrit; the oldest change has one open chain rooted at ``c-root`` on f.py:3."""
    details = build_details_by_change_id(stack_rows_mb_to_head(repo))
    store = ChangeStore(details, web_base=WEB)
    key = next(iter(details))
    root = _comment("c-root", "rename n", unresolved=True, updated="2024-01-01 10:00:00", **root_extra)
    store.set_comments(key, {"f.py": [root]})
    return store, key


def _drafts(store: ChangeStore, key: str) -> list[dict[str, Any]]:
    return [row for rows in store.list_drafts(key).values() for row in rows]


def test_done_writes_a_resolving_draft_on_the_answered_patchset(
    web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, key = _store_with_open_chain(web_stack_repo)
    code, out, _err = run_cli(web_stack_repo, reply_main, ["c-root", "--done", "--json"], monkeypatch, gerrit=store)
    assert code == ExitCode.OK
    payload = json_stdout(out)
    assert (payload["action"], payload["change"], payload["root_id"]) == ("created", key, "c-root")
    assert (payload["patch_set"], payload["unresolved"]) == (1, False)
    assert payload["url"].endswith("/comment/c-root/")
    (draft,) = _drafts(store, key)
    assert draft["in_reply_to"] == "c-root"
    assert (draft["path"], draft["line"], draft["message"], draft["patch_set"]) == ("f.py", 3, "Done", 1)


def test_human_output_says_where_the_draft_went(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, _key = _store_with_open_chain(web_stack_repo)
    code, out, _err = run_cli(
        web_stack_repo, reply_main, ["c-root", "--done", "--color=never"], monkeypatch, gerrit=store
    )
    assert code == ExitCode.OK
    assert "draft reply on f.py:3 (patchset 1) -> resolves" in out
    assert "/comment/c-root/" in out


def test_a_reply_goes_to_the_file_gerrit_named_even_with_a_control_character_in_its_path(
    web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, key = _store_with_open_chain(web_stack_repo)
    odd = "f\x1b.py"
    store.set_comments(key, {odd: store.get_comments(key)["f.py"]})
    code, out, _err = run_cli(
        web_stack_repo, reply_main, ["c-root", "--done", "--color=never"], monkeypatch, gerrit=store
    )
    assert code == ExitCode.OK
    assert "draft reply on f\\x1b.py:3 (patchset 1)" in out
    (draft,) = _drafts(store, key)
    assert draft["path"] == odd


def test_a_reply_without_a_state_flag_keeps_the_chain_open(
    web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, key = _store_with_open_chain(web_stack_repo)
    code, _out, _err = run_cli(web_stack_repo, reply_main, ["c-root", "-m", "Why?"], monkeypatch, gerrit=store)
    assert code == ExitCode.OK
    (draft,) = _drafts(store, key)
    assert draft["unresolved"] is True


def test_replying_again_updates_your_draft_and_shows_what_it_replaced(
    web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, key = _store_with_open_chain(web_stack_repo)
    run_cli(web_stack_repo, reply_main, ["c-root", "-m", "first", "--resolve"], monkeypatch, gerrit=store)
    code, out, _err = run_cli(
        web_stack_repo, reply_main, ["c-root", "-m", "second", "--json"], monkeypatch, gerrit=store
    )
    assert code == ExitCode.OK
    payload = json_stdout(out)
    assert (payload["action"], payload["replaced_message"]) == ("updated", "first")
    # No flag given: the existing draft's state is kept, not reset to the chain's.
    assert payload["unresolved"] is False
    (draft,) = _drafts(store, key)
    assert draft["message"] == "second"


def test_any_comment_names_the_chain_and_the_reply_answers_the_newest(
    web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, key = _store_with_open_chain(web_stack_repo)
    root = _comment("c-root", "rename n", unresolved=True, updated="2024-01-01 10:00:00")
    follow_up = _comment("c-more", "and m", unresolved=True, updated="2024-01-02 10:00:00", in_reply_to="c-root")
    follow_up["patch_set"] = 2
    store.set_comments(key, {"f.py": [root, follow_up]})
    code, _out, _err = run_cli(web_stack_repo, reply_main, ["c-root", "--done"], monkeypatch, gerrit=store)
    assert code == ExitCode.OK
    (draft,) = _drafts(store, key)
    assert (draft["in_reply_to"], draft["patch_set"]) == ("c-more", 2)


def test_reply_on_the_parent_side_stays_on_the_parent_side(
    web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, key = _store_with_open_chain(web_stack_repo, side="PARENT")
    run_cli(web_stack_repo, reply_main, ["c-root", "--done"], monkeypatch, gerrit=store)
    (draft,) = _drafts(store, key)
    assert draft["side"] == "PARENT"


def test_message_from_stdin(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, key = _store_with_open_chain(web_stack_repo)
    monkeypatch.setattr("sys.stdin", io.StringIO("Computing it is cheaper.\n\nSee the benchmark.\n"))
    code, _out, _err = run_cli(web_stack_repo, reply_main, ["c-root", "-F", "-"], monkeypatch, gerrit=store)
    assert code == ExitCode.OK
    (draft,) = _drafts(store, key)
    assert draft["message"] == "Computing it is cheaper.\n\nSee the benchmark."


def test_comment_url_names_its_change(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, key = _store_with_open_chain(web_stack_repo)
    number = store.get_change(key)["_number"]
    url = f"{WEB}/c/testproj/+/{number}/comment/c-root/"
    code, _out, err = run_cli(web_stack_repo, reply_main, [url, "--done"], monkeypatch, gerrit=store)
    assert code == ExitCode.OK, err
    assert len(_drafts(store, key)) == 1


def test_unknown_comment_is_not_found_and_the_open_chains_are_listed(
    web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _key = _store_with_open_chain(web_stack_repo)
    code, _out, err = run_cli(web_stack_repo, reply_main, ["nope", "--done"], monkeypatch, gerrit=store)
    assert code == ExitCode.NOT_FOUND
    assert "no comment nope on the local stack; open chains (id, place, first words):" in err
    assert "  c-root  f.py:3  rename n" in err


@pytest.mark.parametrize("chain", ["f.py:3", "c-ro", "c-root"])
def test_a_chain_is_named_by_its_place_an_id_prefix_or_its_id(
    chain: str, web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What ger show prints (PATH:LINE) and what a human can type (a short id) both answer the chain."""
    store, key = _store_with_open_chain(web_stack_repo)
    code, _out, err = run_cli(web_stack_repo, reply_main, [chain, "--done"], monkeypatch, gerrit=store)
    assert code == ExitCode.OK, err
    (draft,) = _drafts(store, key)
    assert draft["in_reply_to"] == "c-root"


def test_a_place_with_two_open_chains_is_ambiguous_and_lists_them(
    web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, key = _store_with_open_chain(web_stack_repo)
    other = _comment("d-root", "and this", unresolved=True, updated="2024-01-01 11:00:00")
    resolved = _comment("e-root", "old news", unresolved=False, updated="2024-01-01 09:00:00")
    root = _comment("c-root", "rename n", unresolved=True, updated="2024-01-01 10:00:00")
    store.set_comments(key, {"f.py": [root, other, resolved]})
    code, _out, err = run_cli(web_stack_repo, reply_main, ["f.py:3", "--done"], monkeypatch, gerrit=store)
    assert code == ExitCode.AMBIGUOUS
    assert "f.py:3 names 2 comment chains; give one id:" in err
    assert "c-root  f.py:3  rename n" in err and "d-root  f.py:3  and this" in err and "e-root" not in err
    assert _drafts(store, key) == []


def test_discard_removes_your_draft(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, key = _store_with_open_chain(web_stack_repo)
    run_cli(web_stack_repo, reply_main, ["c-root", "-m", "Will do"], monkeypatch, gerrit=store)
    code, out, _err = run_cli(web_stack_repo, reply_main, ["c-root", "--discard"], monkeypatch, gerrit=store)
    assert code == ExitCode.OK
    assert 'discarded draft on f.py:3: "Will do"' in out
    assert _drafts(store, key) == []
    code, _out, _err = run_cli(web_stack_repo, reply_main, ["c-root", "--discard"], monkeypatch, gerrit=store)
    assert code == ExitCode.NOT_FOUND


@pytest.mark.parametrize(
    "argv",
    [
        ["c-root", "--discard", "-m", "x"],
        [f"{WEB}/c/testproj/+/100/comment/c-root/", "--change", "change:100", "--done"],
    ],
)
def test_usage_errors(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
    store, key = _store_with_open_chain(web_stack_repo)
    code, _out, _err = run_cli(web_stack_repo, reply_main, argv, monkeypatch, gerrit=store, catch_sys_exit=True)
    assert code == ExitCode.USAGE
    assert _drafts(store, key) == []


def test_ger_show_reads_back_what_ger_reply_wrote(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The two commands agree on the chain: the reply shows up where show puts drafts."""
    store, key = _store_with_open_chain(web_stack_repo)
    run_cli(web_stack_repo, reply_main, ["c-root", "--done"], monkeypatch, gerrit=store)
    number = store.get_change(key)["_number"]
    code, out, _err = run_cli(web_stack_repo, show_main, ["--json", f"change:{number}"], monkeypatch, gerrit=store)
    assert code in (ExitCode.OK, ExitCode.REFUSED)
    (chain,) = json_stdout(out)["comment_chains"]
    assert (chain["resolved"], chain["resolved_after_publish"]) == (False, True)
    assert [c["draft"] for c in chain["comments"]] == [False, True]


# --- publishing -------------------------------------------------------------------


def _store_with_draft(repo: Path) -> tuple[ChangeStore, str]:
    store, key = _store_with_open_chain(repo)
    store.set_drafts(key, {"f.py": [_comment("d-1", "Done", unresolved=False, updated="", in_reply_to="c-root")]})
    return store, key


def test_publish_with_yes_publishes_every_draft_on_the_stack(
    web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, key = _store_with_draft(web_stack_repo)
    code, out, _err = run_cli(web_stack_repo, reply_main, ["--publish", "-y", "--json"], monkeypatch, gerrit=store)
    assert code == ExitCode.OK
    rows = {row["change"]: row for row in json_stdout(out)["changes"]}
    assert (rows[key]["status"], rows[key]["drafts"]) == ("published", 1)
    assert {row["status"] for k, row in rows.items() if k != key} == {"no-drafts"}
    assert [call.args[0] for call in store.calls_to("publish_drafts")] == [key]
    assert _drafts(store, key) == []


def test_publish_passes_the_change_message(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, _key = _store_with_draft(web_stack_repo)
    run_cli(web_stack_repo, reply_main, ["--publish", "-y", "-m", "Addressed all comments."], monkeypatch, gerrit=store)
    (call,) = store.calls_to("publish_drafts")
    assert call.kwargs["message"] == "Addressed all comments."


def test_dry_run_lists_drafts_and_publishes_nothing(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, _key = _store_with_draft(web_stack_repo)
    code, out, _err = run_cli(web_stack_repo, reply_main, ["--publish", "--dry-run"], monkeypatch, gerrit=store)
    assert code == ExitCode.OK
    assert 'f.py:3 -> resolves: "Done"' in out
    assert store.calls_to("publish_drafts") == []


def test_publish_without_a_terminal_needs_yes(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, key = _store_with_draft(web_stack_repo)
    code, _out, err = run_cli(web_stack_repo, reply_main, ["--publish"], monkeypatch, gerrit=store)
    assert code == ExitCode.USAGE
    assert "pass -y" in err
    assert store.calls_to("publish_drafts") == []
    assert len(_drafts(store, key)) == 1


@pytest.mark.parametrize(("answer", "published"), [("y\n", True), ("\n", False), ("n\n", False)])
def test_publish_prompt_defaults_to_no(
    web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch, answer: str, published: bool
) -> None:
    store, _key = _store_with_draft(web_stack_repo)
    monkeypatch.setattr("gerrit_workflow_tools.cli_common.stdin_is_terminal", lambda: True)
    monkeypatch.setattr("sys.stdin", io.StringIO(answer))
    code, _out, _err = run_cli(web_stack_repo, reply_main, ["--publish"], monkeypatch, gerrit=store)
    assert code == (ExitCode.OK if published else ExitCode.REFUSED)
    assert bool(store.calls_to("publish_drafts")) is published


def test_publish_with_ai_needs_yes_even_on_a_terminal(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, _key = _store_with_draft(web_stack_repo)
    monkeypatch.setattr("gerrit_workflow_tools.cli_common.stdin_is_terminal", lambda: True)
    monkeypatch.setattr("sys.stdin", io.StringIO("y\n"))
    code, _out, err = run_cli(web_stack_repo, reply_main, ["--publish", "--ai"], monkeypatch, gerrit=store)
    assert code == ExitCode.USAGE
    assert "pass -y" in err
    assert not store.calls_to("publish_drafts")


def test_publish_guard_blocks_before_contacting_gerrit(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, _key = _store_with_draft(web_stack_repo)
    monkeypatch.setenv("GER_NO_PUBLISH", "1")
    code, _out, err = run_cli(
        web_stack_repo, reply_main, ["--publish", "-y"], monkeypatch, gerrit=store, catch_sys_exit=True
    )
    assert code == ExitCode.USAGE
    assert "GER_NO_PUBLISH" in err
    assert store.calls == []
    # Looking is still allowed.
    code, _out, _err = run_cli(web_stack_repo, reply_main, ["--publish", "--dry-run"], monkeypatch, gerrit=store)
    assert code == ExitCode.OK


def test_a_failed_change_does_not_stop_the_others(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, first = _store_with_draft(web_stack_repo)
    second = next(k for k in build_details_by_change_id(stack_rows_mb_to_head(web_stack_repo)) if k != first)
    store.set_comments(second, {"g.py": [_comment("c-2", "why", unresolved=True, updated="2024-01-01 10:00:00")]})
    store.set_drafts(second, {"g.py": [_comment("d-2", "because", unresolved=True, updated="", in_reply_to="c-2")]})
    real = store.publish_drafts

    def flaky(change_id: str, **kwargs: Any) -> dict[str, Any]:
        if change_id == first:
            raise GerritApiError("Gerrit HTTP 409", status=409)
        return real(change_id, **kwargs)

    monkeypatch.setattr(store, "publish_drafts", flaky)
    code, out, err = run_cli(web_stack_repo, reply_main, ["--publish", "-y", "--json"], monkeypatch, gerrit=store)
    assert code == ExitCode.GERRIT
    rows = {row["change"]: row for row in json_stdout(out)["changes"]}
    assert rows[first]["status"] == "failed" and "409" in rows[first]["error"]
    assert rows[second]["status"] == "published"
    assert first in err


def test_ger_show_sees_the_published_chain_at_once(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Publishing drops the cached rows, so show does not serve the pre-publish comments."""
    store, key = _store_with_open_chain(web_stack_repo)
    number = store.get_change(key)["_number"]
    run_cli(web_stack_repo, reply_main, ["c-root", "--done"], monkeypatch, gerrit=store)
    run_cli(web_stack_repo, show_main, ["--json", f"change:{number}"], monkeypatch, gerrit=store)
    run_cli(web_stack_repo, reply_main, ["--publish", "-y"], monkeypatch, gerrit=store)
    _code, out, _err = run_cli(
        web_stack_repo, show_main, ["--json", "--comments", "all", f"change:{number}"], monkeypatch, gerrit=store
    )
    (chain,) = json_stdout(out)["comment_chains"]
    assert chain["resolved"] is True
    assert [c["draft"] for c in chain["comments"]] == [False, False]


def test_publish_only_sends_the_named_draft_and_keeps_the_rest(
    web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--only``: an agent can send its reply without publishing the user's own pending draft."""
    store, key = _store_with_open_chain(web_stack_repo)
    store.set_drafts(
        key,
        {
            "f.py": [
                _comment("d-1", "Done", unresolved=False, updated="", in_reply_to="c-root"),
                _comment("d-2", "the user's own draft", unresolved=True, updated=""),
            ]
        },
    )
    code, _out, err = run_cli(
        web_stack_repo, reply_main, ["--publish", "--only", "d-1", "-y"], monkeypatch, gerrit=store
    )
    assert code == ExitCode.OK, err
    (call,) = store.calls_to("publish_drafts")
    assert call.kwargs["draft_ids"] == ["d-1"]
    assert [row["id"] for row in _drafts(store, key)] == ["d-2"]


def test_publish_only_an_unknown_draft_is_not_found(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, _key = _store_with_draft(web_stack_repo)
    code, _out, err = run_cli(
        web_stack_repo, reply_main, ["--publish", "--only", "nope", "-y"], monkeypatch, gerrit=store
    )
    assert code == ExitCode.NOT_FOUND
    assert "no draft of yours in a chain with id nope" in err
    assert store.calls_to("publish_drafts") == []


def test_publish_a_message_alone_on_a_named_change(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A change-level note without a vote and without publishing anything else."""
    store, key = _store_with_open_chain(web_stack_repo)
    code, out, err = run_cli(
        web_stack_repo,
        reply_main,
        ["--publish", key, "-m", "recheck: tests pass locally", "-y"],
        monkeypatch,
        gerrit=store,
    )
    assert code == ExitCode.OK, err
    (call,) = store.calls_to("publish_drafts")
    assert (call.kwargs["message"], call.kwargs["labels"], call.kwargs["draft_ids"]) == (
        "recheck: tests pass locally",
        None,
        [],
    )
    assert "published 0 draft(s) and a message" in out


def test_publish_a_message_never_goes_to_every_change_of_the_stack(
    web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _key = _store_with_open_chain(web_stack_repo)
    code, out, _err = run_cli(web_stack_repo, reply_main, ["--publish", "-m", "note", "-y"], monkeypatch, gerrit=store)
    assert code == ExitCode.OK
    assert store.calls_to("publish_drafts") == []
    assert "no drafts to publish" in out


def test_publish_with_a_vote(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, key = _store_with_draft(web_stack_repo)
    code, _out, err = run_cli(
        web_stack_repo, reply_main, ["--publish", "--vote", "CR+1", "-y"], monkeypatch, gerrit=store
    )
    assert code == ExitCode.OK, err
    (call,) = store.calls_to("publish_drafts")
    assert (call.args[0], call.kwargs["labels"], call.kwargs["draft_ids"]) == (key, {"Code-Review": 1}, None)


def test_ger_assume_yes_publishes_without_a_prompt(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, _key = _store_with_draft(web_stack_repo)
    monkeypatch.setenv("GER_ASSUME_YES", "1")
    code, _out, err = run_cli(web_stack_repo, reply_main, ["--publish"], monkeypatch, gerrit=store)
    assert code == ExitCode.OK, err
    assert len(store.calls_to("publish_drafts")) == 1


def test_publish_takes_a_bare_change_number(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Publishing acts on changes: ``ger reply --publish 100`` names change 100, not a commit."""
    store, key = _store_with_open_chain(web_stack_repo)
    number = store.get_change(key)["_number"]
    code, _out, err = run_cli(
        web_stack_repo,
        reply_main,
        ["--publish", str(number), "--keep-drafts", "-m", "note", "-y"],
        monkeypatch,
        gerrit=store,
    )
    assert code == ExitCode.OK, err
    (call,) = store.calls_to("publish_drafts")
    assert (call.args[0], call.kwargs["draft_ids"]) == (key, [])


def test_publish_only_takes_the_chain_you_replied_to(web_stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``ger reply c-root …`` then ``--only c-root``: the id you just used names your draft there."""
    store, key = _store_with_draft(web_stack_repo)
    code, _out, err = run_cli(
        web_stack_repo, reply_main, ["--publish", "--only", "c-root", "-y"], monkeypatch, gerrit=store
    )
    assert code == ExitCode.OK, err
    (call,) = store.calls_to("publish_drafts")
    assert (call.args[0], call.kwargs["draft_ids"]) == (key, ["d-1"])
